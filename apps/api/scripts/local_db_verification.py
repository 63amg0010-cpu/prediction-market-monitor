"""Read-only state assertions for the guarded local migration database."""

# pyright: reportAny=false
# ruff: noqa: EM101

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.local_db_guard import LocalDatabaseHoldError

ROOT: Final = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG: Final = ROOT / "apps" / "api" / "alembic.ini"
MANIFOLD_COUNT_SQL: Final = (
    "SELECT count(*) FROM community_sources WHERE platform::text = 'manifold'"
)
GENERATED_COLUMN_SQL: Final = (
    "SELECT attgenerated='s' FROM pg_attribute "
    "WHERE attrelid='post_versions'::regclass AND attname='search_text'"
)
INDEX_SEMANTICS_SQL: Final = """
WITH named AS (
    SELECT i.*, idx.oid AS index_oid, idx_ns.nspname AS index_namespace,
           tbl.oid AS table_oid, tbl.relname AS table_name,
           tbl_ns.nspname AS table_namespace, am.amname AS access_method
    FROM pg_class AS idx
    JOIN pg_namespace AS idx_ns ON idx_ns.oid = idx.relnamespace
    JOIN pg_index AS i ON i.indexrelid = idx.oid
    JOIN pg_class AS tbl ON tbl.oid = i.indrelid
    JOIN pg_namespace AS tbl_ns ON tbl_ns.oid = tbl.relnamespace
    JOIN pg_am AS am ON am.oid = idx.relam
    WHERE idx.relkind = 'i' AND idx.relname = :name
),
keyed AS (
    SELECT named.*, key.ordinality, attribute.attname,
           column_collation.collname AS column_collation,
           index_collation.collname AS index_collation,
           opclass.opcname AS opclass
    FROM named
    CROSS JOIN LATERAL unnest(
        named.indkey::smallint[],
        named.indcollation::oid[],
        named.indclass::oid[]
    ) WITH ORDINALITY AS key(attnum, collation_oid, opclass_oid, ordinality)
    LEFT JOIN pg_attribute AS attribute
      ON attribute.attrelid = named.table_oid
     AND attribute.attnum = key.attnum
    LEFT JOIN pg_collation AS column_collation
      ON column_collation.oid = attribute.attcollation
    LEFT JOIN pg_collation AS index_collation
      ON index_collation.oid = key.collation_oid
    LEFT JOIN pg_opclass AS opclass ON opclass.oid = key.opclass_oid
)
SELECT (SELECT count(*) FROM named) = 1 AS unique_named_index,
       COALESCE(bool_and(
           table_namespace = 'public' AND index_namespace = 'public'
           AND table_name = 'post_versions'
       ), false) AS exact_target,
       COALESCE(bool_and(
           indnkeyatts = 1 AND indnatts = 1 AND ordinality = 1
           AND attname = 'search_text' AND indexprs IS NULL
       ), false) AS exact_column,
       COALESCE(bool_and(column_collation = 'C'), false)
           AS exact_column_collation,
       COALESCE(bool_and(index_collation = 'C'), false)
           AS exact_index_collation,
       COALESCE(bool_and(opclass = 'gin_trgm_ops'), false) AS exact_opclass,
       COALESCE(bool_and(access_method = 'gin'), false) AS exact_access_method,
       COALESCE(bool_and(indisvalid AND indisready AND NOT indisunique), false)
           AS valid_ready,
       COALESCE(bool_and(indpred IS NULL), false) AS nonpartial
FROM keyed
"""
INDEX_SEMANTIC_FIELDS: Final = frozenset(
    {
        "unique_named_index",
        "exact_target",
        "exact_column",
        "exact_column_collation",
        "exact_index_collation",
        "exact_opclass",
        "exact_access_method",
        "valid_ready",
        "nonpartial",
    }
)


async def baseline_checks(url: str, expected_revision: str) -> dict[str, object]:
    """Require exact 0009 and prove that no Manifold state exists."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            current = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            manifold = await connection.scalar(
                text(MANIFOLD_COUNT_SQL)
            )
    finally:
        await engine.dispose()
    if current != expected_revision:
        raise LocalDatabaseHoldError("required_start_revision_mismatch")
    if manifold != 0:
        raise LocalDatabaseHoldError("baseline_manifold_state_present")
    return {"current_revision": str(current), "manifold_rows": 0}


async def current_revision(url: str) -> str:
    """Read the one Alembic version row from the disposable database."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
    finally:
        await engine.dispose()
    return str(value)


async def verify_final(
    *,
    url: str,
    expected_head: str,
    expected_current: str,
    expected_index: str,
) -> dict[str, object]:
    """Verify exact 0011 schema, inert source, and measured GIN index."""
    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG))).get_heads()
    current = await current_revision(url)
    if heads != [expected_head] or current != expected_current:
        raise LocalDatabaseHoldError("migration_head_or_current_mismatch")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            inert = (
                await connection.execute(
                    text(
                        """
                        SELECT NOT enabled
                               AND active_authorization_id IS NULL
                               AND current_budget_id IS NULL
                               AND current_binding_id IS NULL
                               AND current_cadence_id IS NULL
                        FROM community_sources
                        WHERE platform::text='manifold'
                          AND external_key='manifold-comments'
                        """
                    )
                )
            ).scalar_one_or_none()
            function_sql = await connection.scalar(
                text("SELECT pg_get_functiondef('search_fold_v1(text)'::regprocedure)")
            )
            index_semantics = (
                await connection.execute(
                    text(INDEX_SEMANTICS_SQL),
                    {"name": expected_index},
                )
            ).mappings().one()
            index_bytes = await connection.scalar(
                text("SELECT pg_relation_size(to_regclass(:name))"),
                {"name": expected_index},
            )
            generated = await connection.scalar(
                text(GENERATED_COLUMN_SQL)
            )
            attestations = await connection.scalar(
                text("SELECT count(*) FROM source_activation_attestations")
            )
    finally:
        await engine.dispose()
    if (
        inert is not True
        or generated is not True
        or not isinstance(function_sql, str)
        or "IMMUTABLE" not in function_sql
        or "PARALLEL SAFE" not in function_sql
        or not _index_semantics_valid(index_semantics)
        or not isinstance(index_bytes, int)
        or index_bytes <= 0
        or not isinstance(attestations, int)
        or attestations < 1
    ):
        raise LocalDatabaseHoldError("local_schema_verification_failed")
    return {
        "current_revision": current,
        "sole_head": expected_head,
        "manifold_inert": True,
        "attestation_present": True,
        "search_function_exact": True,
        "generated_column": True,
        "index": expected_index,
        "index_bytes": index_bytes,
    }


def _index_semantics_valid(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    semantics = cast("Mapping[str, object]", value)
    return all(semantics.get(field) is True for field in INDEX_SEMANTIC_FIELDS)


__all__ = (
    "INDEX_SEMANTICS_SQL",
    "baseline_checks",
    "current_revision",
    "verify_final",
)
