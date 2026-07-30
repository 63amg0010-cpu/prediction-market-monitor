# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "asyncpg>=0.30,<1", "httpx2[brotli,http2,zstd]>=2.5,<3",
#   "orjson>=3.10,<4", "pydantic>=2.10,<3",
# ]
# ///
# ─── How to run ───
# uv run --package monitor-api python apps/api/scripts/manifold_evidence.py --help
"""Create and verify privacy-minimized Manifold authorization evidence."""

from __future__ import annotations

import socket
import sys
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Final

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.configuration.manifold_evidence import (
    API_ORIGIN,
    COMMENTS_ROUTE,
    JSON_DOCUMENT,
    JSON_VALUE,
    MARKETS_ROUTE,
    PROBE_REQUEST_COUNT,
    ROUTES,
    AuthorizationRecord,
    CliArgs,
    CommentContent,
    CommentProjection,
    JsonDocument,
    LiveProof,
    MarketProjection,
    canonical_bytes,
    database_url,
    load_record,
    parse_market_url,
    tiptap_plain_text,
    verify_record,
)
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from scripts.manifold_evidence_db import db_now as _db_now
from scripts.manifold_evidence_db import run_database_time
from scripts.manifold_evidence_io import required as _required
from scripts.manifold_evidence_io import write as _write

_LIMITS: Final[httpx2.Limits] = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
_TIMEOUT: Final[httpx2.Timeout] = httpx2.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)
_SOCKET_OPTIONS: Final[list[tuple[int, int, int]]] = [
    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
]


def _mark_request(request: httpx2.Request) -> None:
    request.extensions["start"] = time.perf_counter()


def _client() -> httpx2.Client:
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=_LIMITS,
        socket_options=_SOCKET_OPTIONS,
    )
    return httpx2.Client(
        transport=transport,
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"user-agent": "prediction-market-monitor-evidence/1"},
        event_hooks={"request": [_mark_request]},
    )


def _probe(prepared_at: datetime) -> LiveProof:
    with _client() as client:
        markets_response = client.get(
            f"{API_ORIGIN}{MARKETS_ROUTE}",
            params={"limit": 20, "sort": "last-comment-time", "order": "desc"},
        )
        _ = markets_response.raise_for_status()
        markets = JSON_VALUE.validate_json(markets_response.content)
        if not isinstance(markets, list) or not markets:
            message = "markets route returned no usable records"
            raise RuntimeError(message)
        market_raw = next((item for item in markets if isinstance(item, dict)), None)
        if market_raw is None:
            message = "markets route projection is unsafe"
            raise RuntimeError(message)
        market_id = market_raw.get("id")
        question = market_raw.get("question")
        provider_url = market_raw.get("url")
        values = (market_id, question, provider_url)
        if not all(isinstance(item, str) and item for item in values):
            message = "market projection is incomplete"
            raise RuntimeError(message)
        market_id = str(market_id)
        question = str(question)
        provider_url = str(provider_url)
        market_slug, neutral_url = parse_market_url(provider_url)
        comments_response = client.get(
            f"{API_ORIGIN}{COMMENTS_ROUTE}",
            params={"contractId": market_id, "limit": 20, "order": "newest"},
        )
        _ = comments_response.raise_for_status()
        comments = JSON_VALUE.validate_json(comments_response.content)
        if not isinstance(comments, list) or not comments:
            message = "comments route returned no usable records"
            raise RuntimeError(message)
        comment_raw = next((item for item in comments if isinstance(item, dict)), None)
        if comment_raw is None:
            message = "comment projection is unsafe"
            raise RuntimeError(message)
        comment_id = comment_raw.get("id")
        contract_id = comment_raw.get("contractId")
        created_time = comment_raw.get("createdTime")
        content = comment_raw.get("content")
        text = (
            tiptap_plain_text(content).strip()
            if isinstance(content, (dict, list))
            else ""
        )
        if (
            not isinstance(comment_id, str)
            or contract_id != market_id
            or not isinstance(created_time, int)
            or not text
        ):
            message = "comment projection is incomplete"
            raise RuntimeError(message)
        neutral_response = client.get(neutral_url)
        _ = neutral_response.raise_for_status()
        resolved = market_id in neutral_response.text
    market = MarketProjection(
        id=market_id,
        question=question,
        market_slug=market_slug,
        neutral_url=neutral_url,
    )
    comment = CommentProjection(
        id=comment_id,
        contractId=market_id,
        createdTime=created_time,
        content=CommentContent(text=text),
    )
    projection: JsonDocument = {
        "market": JSON_DOCUMENT.validate_python(market.model_dump(mode="json")),
        "comment": JSON_DOCUMENT.validate_python(comment.model_dump(mode="json")),
    }
    return LiveProof(
        schema="manifold.live-proof.v1",
        prepared_at=prepared_at,
        routes=ROUTES,
        market=market,
        comment=comment,
        neutral_url_resolves_to_market_id=resolved,
        request_count=PROBE_REQUEST_COUNT,
        raw_body_persisted=False,
        projection_sha256=sha256(canonical_bytes(projection)).hexdigest(),
    )


def verify_command(args: CliArgs, *, refresh: bool) -> int:
    """Verify persisted or freshly probed evidence using one database time."""
    connection_url = database_url(args.database_url_env)
    db_now = run_database_time(_db_now(connection_url))
    evidence_path = _required(args.evidence, "--evidence")
    record = load_record(Path(evidence_path), AuthorizationRecord)
    if refresh:
        proof = _probe(db_now)
    else:
        live_path = _required(args.live_proof, "--live-proof")
        proof = load_record(Path(live_path), LiveProof)
    authorized, reasons = verify_record(record, proof, db_now)
    receipt: JsonDocument = {
        "schema": "manifold.authorization-receipt.v1",
        "authorized_for_activation": authorized,
        "created_at_db": db_now.isoformat().replace("+00:00", "Z"),
        "db_now": db_now.isoformat().replace("+00:00", "Z"),
        "evidence_sha256": record.sha256,
        "live_projection_sha256": proof.projection_sha256,
        "reasons": list(reasons),
    }
    output_path = _required(args.json_out, "--json-out")
    _write(Path(output_path), receipt)
    return 0 if authorized else 1


def parse_cli(argv: list[str]) -> CliArgs:
    """Parse flag/value pairs into the closed CLI model."""
    if not argv or len(argv[1:]) % 2:
        message = "expected a command followed by flag/value pairs"
        raise RuntimeError(message)
    raw: JsonDocument = {"command": argv[0]}
    for index in range(1, len(argv), 2):
        flag = argv[index]
        if not flag.startswith("--"):
            message = f"invalid flag: {flag}"
            raise RuntimeError(message)
        raw[flag[2:].replace("-", "_")] = argv[index + 1]
    return CliArgs.model_validate(raw)


def main() -> int:
    """Run one evidence CLI subcommand."""
    args = parse_cli(sys.argv[1:])
    match args.command:  # noqa: MATCH_OK -- Literal command union is exhaustive.
        case "probe":
            proof = _probe(datetime.now(UTC))
            document = JSON_DOCUMENT.validate_json(proof.model_dump_json(by_alias=True))
            output_path = _required(args.output, "--output")
            _write(Path(output_path), document)
            return 0
        case "verify":
            return verify_command(args, refresh=False)
        case "refresh":
            return verify_command(args, refresh=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        ValidationError,
        SQLAlchemyError,
        httpx2.HTTPError,
    ) as error:
        _ = sys.stderr.write(f"manifold evidence HOLD: {error}\n")
        raise SystemExit(2) from None
