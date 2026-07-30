"""One-transaction SQLAlchemy reader for the Production probe."""

# pyright: reportAny=false, reportArgumentType=false, reportCallIssue=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# ruff: noqa: D101, EM101, FBT003, TC003

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast

from app.services.dashboard.filters import search_fold_v1, search_like_pattern_v1

from . import runtime_production_adapter_sql as sql
from .release_chain_common import ReleaseChainError
from .release_production_models import DatabaseProof, SearchProof

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from .runtime_production_adapter_evidence import PreparedEvidence


@dataclass(frozen=True, slots=True)
class ProductionDatabaseSnapshot:
    database: DatabaseProof
    search: SearchProof
    literal: str
    negative_literal: str
    keyword: str
    source_id: str
    page_ids: tuple[str, ...]


async def read_database(
    engine: AsyncEngine,
    evidence: PreparedEvidence,
) -> ProductionDatabaseSnapshot:
    """Read the exact state and search contracts from one immutable snapshot."""
    async with engine.connect() as connection, connection.begin():
        _ = await connection.execute(sql.READ_ONLY)
        now = await connection.scalar(sql.DATABASE_NOW)
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ReleaseChainError("production_database_time_invalid")
        state = await _one(
            connection, sql.STATE, {"nonce": evidence.bindings.activation_nonce}
        )
        dc_before = await _one(connection, sql.DCINSIDE)
        seed = await _one(connection, sql.SEED)
        literal = _literal(seed.get("literal"), seed.get("keyword"))
        keyword = seed.get("keyword")
        source_id = seed.get("source_id")
        if (
            not isinstance(keyword, str)
            or not keyword
            or not isinstance(source_id, str)
        ):
            raise ReleaseChainError("production_search_seed_invalid")
        pattern = search_like_pattern_v1(search_fold_v1(literal).value)
        negative = f"{literal}-no-match-{evidence.release_chain_sha256[:12]}"
        negative_pattern = search_like_pattern_v1(search_fold_v1(negative).value)
        base = {"source_id": source_id, "pattern": pattern, "keyword": None}
        positive = _integer(await _one(connection, sql.COUNT, base), "total")
        first = await _all(connection, sql.PAGE, base)
        repeat = await _all(connection, sql.PAGE, base)
        negative_total = _integer(
            await _one(
                connection,
                sql.COUNT,
                {"source_id": source_id, "pattern": negative_pattern, "keyword": None},
            ),
            "total",
        )
        keyword_total = _integer(
            await _one(
                connection,
                sql.COUNT,
                {"source_id": source_id, "pattern": None, "keyword": keyword},
            ),
            "total",
        )
        and_total = _integer(
            await _one(
                connection,
                sql.COUNT,
                {"source_id": source_id, "pattern": pattern, "keyword": keyword},
            ),
            "total",
        )
        freshness = await _one(
            connection, sql.FRESHNESS, {"nonce": evidence.bindings.activation_nonce}
        )
        dc_after = await _one(connection, sql.DCINSIDE)
    before_hash, after_hash = _row_hash(dc_before), _row_hash(dc_after)
    ids = tuple(str(item["id"]) for item in first)
    database = DatabaseProof(
        revision=str(state["revision"]),
        transaction_read_only=True,
        writes_observed=0,
        reviewed_sha=str(state["reviewed_sha"]),
        approved_plan_sha256=str(state["approved_plan_sha256"]),
        activation_nonce=str(state["activation_nonce"]),
        release_chain_sha256=evidence.release_chain_sha256,
        attestation_sha256=str(state["attestation_sha256"]),
        free_tier_sha256=str(state["free_tier_evidence_sha256"]),
        source_state=str(state["source_state"]),
        source_enabled=state["source_enabled"] is True,
        binding_verified=state["binding_verified"] is True,
        source_id_sha256=sha256(str(state["source_id"]).encode()).hexdigest(),
        cadence_anchor_at=_time(state["cadence_anchor_at"]),
        authorization_expires_at=_time(state["authorization_expires_at"]),
        dcinside_before_sha256=before_hash,
        dcinside_current_sha256=after_hash,
        dcinside_query_ok=dc_before == dc_after,
        dcinside_90d_count=_integer(dc_after, "count_90d"),
    )
    latest = _time(freshness["latest_manifold_at"])
    search = SearchProof(
        "production",
        True,
        False,
        False,
        False,
        True,
        sha256(literal.encode()).hexdigest(),
        sha256(negative.encode()).hexdigest(),
        positive,
        len(first),
        all(str(row["source_id"]) == source_id for row in first),
        negative_total,
        1,
        50,
        True,
        first == repeat,
        True,
        keyword_total,
        and_total,
        True,
        True,
        False,
        False,
        True,
        latest,
        now,
        freshness["dcinside_recent"] is True,
        freshness["cadence_complete"] is True,
    )
    return ProductionDatabaseSnapshot(
        database, search, literal, negative, keyword, source_id, ids
    )


async def _one(
    connection: AsyncConnection,
    statement: object,
    params: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    result = await connection.execute(statement, params or {})
    return cast("Mapping[str, object]", result.mappings().one())


async def _all(
    connection: AsyncConnection,
    statement: object,
    params: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    result = await connection.execute(statement, params)
    return tuple(cast("Mapping[str, object]", row) for row in result.mappings().all())


def _literal(value: object, keyword: object) -> str:
    if not isinstance(value, str):
        raise ReleaseChainError("production_search_seed_invalid")
    candidates = re.findall(r"[\w-]{2,}", value, flags=re.UNICODE)
    selected = next(
        (item for item in candidates if item.casefold() != str(keyword).casefold()), ""
    )
    if not selected:
        raise ReleaseChainError("production_search_literal_not_arbitrary")
    return selected[:100]


def _integer(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReleaseChainError("production_database_row_invalid")
    return value


def _time(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ReleaseChainError("production_database_time_invalid")
    return value


def _row_hash(row: Mapping[str, object]) -> str:
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(raw).hexdigest()


__all__ = ("ProductionDatabaseSnapshot", "read_database")
