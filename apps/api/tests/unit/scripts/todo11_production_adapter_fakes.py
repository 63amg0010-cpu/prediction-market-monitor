"""Typed fake SQLAlchemy and HTTPS boundaries for Production adapter tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, final
from urllib.parse import parse_qs, urlsplit

from scripts.release_chain_common import Bindings
from scripts.release_production_models import ProductionProbeQuery
from scripts.runtime_production_adapter_evidence import (
    PreparedDeployment,
    PreparedEvidence,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

NOW = datetime(2026, 7, 29, 3, tzinfo=UTC)
SHA, PLAN = "a" * 40, "b" * 64
NONCE = "11111111-1111-4111-8111-111111111111"
SOURCE = "0890756a-ca23-5697-ae4c-0de527361064"


@final
class Result:
    def __init__(
        self,
        one: dict[str, object] | None = None,
        all_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._one = one or {}
        self._all = all_rows or []

    def mappings(self) -> Result:
        return self

    def one(self) -> dict[str, object]:
        return self._one

    def all(self) -> list[dict[str, object]]:
        return self._all


@final
class Context[T]:
    def __init__(self, value: T) -> None:
        self.value = value

    async def __aenter__(self) -> T:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


@final
class Connection:
    def __init__(self, evidence: PreparedEvidence) -> None:
        self.evidence = evidence
        self.calls: list[str] = []

    def begin(self) -> Context[Connection]:
        return Context(self)

    async def scalar(self, statement: object) -> object:
        self.calls.append(str(statement))
        return NOW

    async def execute(
        self, statement: object, params: dict[str, object] | None = None
    ) -> Result:
        sql = str(statement)
        self.calls.append(sql)
        result = Result()
        if "WITH latest AS" in sql:
            result = Result(self._state())
        elif "platform::text = 'dcinside'" in sql and "GROUP BY" in sql:
            result = Result(
                {"source_id": "dc", "enabled": True, "count_90d": 4, "snapshot": []}
            )
        elif "pv.title AS literal" in sql:
            result = Result(
                {
                    "source_id": SOURCE,
                    "literal": "Arbitrary market title",
                    "keyword": "rule",
                }
            )
        elif "SELECT CAST(p.id AS text)" in sql:
            rows: list[dict[str, object]] = [
                {"id": f"p{index}", "source_id": SOURCE} for index in range(1, 4)
            ]
            result = Result(all_rows=rows)
        elif "SELECT count(*) AS total" in sql:
            result = Result({"total": _total(params or {})})
        elif "latest_manifold_at" in sql:
            result = Result(
                {
                    "latest_manifold_at": NOW - timedelta(hours=1),
                    "dcinside_recent": True,
                    "cadence_complete": False,
                }
            )
        return result

    def _state(self) -> dict[str, object]:
        return {
            "revision": "20260727_0011",
            "reviewed_sha": SHA,
            "approved_plan_sha256": PLAN,
            "activation_nonce": NONCE,
            "source_state": "active",
            "source_enabled": True,
            "binding_verified": True,
            "source_id": SOURCE,
            "cadence_anchor_at": NOW - timedelta(days=1),
            "authorization_expires_at": NOW + timedelta(days=30),
            "attestation_sha256": self.evidence.attestation_sha256,
            "free_tier_evidence_sha256": self.evidence.free_tier_sha256,
        }


@final
class Engine:
    def __init__(self, evidence: PreparedEvidence) -> None:
        self.connection = Connection(evidence)
        self.disposed = False

    def connect(self) -> Context[Connection]:
        return Context(self.connection)

    async def dispose(self) -> None:
        self.disposed = True


@final
class Response:
    status_code: int
    content: bytes
    headers: Mapping[str, str]

    def __init__(self, value: object, *, html: bool = False) -> None:
        self.status_code = 200
        self.content = (
            b"<html>login</html>"
            if html
            else json.dumps(value, separators=(",", ":")).encode()
        )
        self.headers = {"content-length": str(len(self.content))}


@final
class FakeHttp:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> Response:
        self.urls.append(url)
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path == "/v1/health":
            return Response({"status": "ok", "db": "ok"})
        if parsed.path == "/login":
            return Response({}, html=True)
        if parsed.path == "/v1/dashboard":
            return Response(
                {
                    "sources": [
                        {
                            "source_id": SOURCE,
                            "enabled": True,
                            "latest_successful_run_at": NOW.isoformat(),
                        }
                    ]
                }
            )
        total = _http_total(query)
        items = [{"id": f"p{index}"} for index in range(1, 4)] if total == 3 else []
        return Response(
            {"items": items, "page": {"page": 1, "page_size": 50, "total_items": total}}
        )


def evidence() -> PreparedEvidence:
    bindings = Bindings(SHA, PLAN, "c" * 64, ("d" * 64, "e" * 64), NONCE)
    api = PreparedDeployment(
        kind="api",
        project_name="prediction-monitor-api",
        project_identity_sha256="4" * 64,
        deployment_identity_sha256="A" * 64,
        team_identity_sha256="9" * 64,
        state="READY",
        production=True,
        reviewed_sha=SHA,
    )
    web = PreparedDeployment(
        kind="web",
        project_name="prediction-monitor-web",
        project_identity_sha256="5" * 64,
        deployment_identity_sha256="B" * 64,
        team_identity_sha256="9" * 64,
        state="READY",
        production=True,
        reviewed_sha=SHA,
    )
    return PreparedEvidence(bindings, "f" * 64, "1" * 64, "2" * 64, (api, web))


def query() -> ProductionProbeQuery:
    return ProductionProbeQuery(
        "DB_URL",
        "https://api.example.com",
        "https://web.example.com",
        SHA,
        "20260727_0011",
    )


def _total(values: dict[str, object]) -> int:
    pattern, keyword = str(values.get("pattern")), values.get("keyword")
    if "no-match" in pattern:
        return 0
    if keyword and values.get("pattern"):
        return 1
    return 2 if keyword else 3


def _http_total(query_values: dict[str, list[str]]) -> int:
    search = query_values.get("search", [""])[0]
    keyword = query_values.get("keyword", [""])[0]
    if "no-match" in search:
        return 0
    if search and keyword:
        return 1
    return 2 if keyword else 3


__all__ = (
    "NONCE",
    "PLAN",
    "SHA",
    "Engine",
    "FakeHttp",
    "evidence",
    "query",
)
