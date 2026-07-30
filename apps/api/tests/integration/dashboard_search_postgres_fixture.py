from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, Final
from uuid import UUID

import pytest
from app.services.dashboard.filters import search_fold_v1, search_like_pattern_v1
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

DATABASE_URL_ENV: Final = "MIGRATION_QA_DATABASE_URL"
NOW: Final = datetime(2026, 7, 28, 0, tzinfo=UTC)
SOURCE_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
INDEX_NAME: Final = "ix_post_versions_search_text_trgm"


class PostgresExplainIndexPlan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    index_name: str = Field(alias="Index Name")


class PostgresExplainHeapPlan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    plans: tuple[PostgresExplainIndexPlan, ...] = Field(alias="Plans")


class PostgresExplainDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    plan: PostgresExplainHeapPlan = Field(alias="Plan")


class PostgresExplainRow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    plan: tuple[PostgresExplainDocument, ...] = Field(alias="QUERY PLAN")


_SCHEMA_SQL: Final = (
    "DROP EXTENSION IF EXISTS pg_trgm CASCADE",
    "DROP SCHEMA public CASCADE",
    "CREATE SCHEMA public",
    "CREATE EXTENSION pg_trgm",
    """
    CREATE FUNCTION search_fold_v1(input text)
    RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $search_fold_v1$
    SELECT translate(normalize(btrim(input,
        chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(32)), NFC),
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C"
    $search_fold_v1$
    """,
    """
    CREATE TABLE community_sources (
        id uuid PRIMARY KEY,
        display_name text NOT NULL,
        country text NOT NULL,
        enabled boolean NOT NULL
    )
    """,
    """
    CREATE TABLE post_versions (
        id uuid PRIMARY KEY,
        title text NOT NULL,
        body text NOT NULL,
        search_text text COLLATE "C" GENERATED ALWAYS AS
            (search_fold_v1(coalesce(title, '') || E'\n' || coalesce(body, '')))
            STORED
    )
    """,
    f"""
    CREATE INDEX {INDEX_NAME} ON post_versions
    USING gin ((search_text COLLATE "C") gin_trgm_ops)
    """,
    """
    CREATE TABLE posts (
        id uuid PRIMARY KEY,
        source_id uuid NOT NULL,
        current_version_id uuid NOT NULL,
        canonical_url text NOT NULL,
        published_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE post_matches (
        post_version_id uuid NOT NULL,
        matched boolean NOT NULL,
        normalized_phrase text NOT NULL
    )
    """,
    """
    CREATE TABLE analyses (
        id uuid PRIMARY KEY,
        post_version_id uuid NOT NULL,
        analyzed_at timestamptz NOT NULL,
        state text NOT NULL,
        relevance boolean,
        sentiment text
    )
    """,
    """
    CREATE TABLE analysis_queue (
        post_version_id uuid NOT NULL,
        status text NOT NULL
    )
    """,
    """
    CREATE TABLE engagement_observations (
        id uuid PRIMARY KEY,
        post_version_id uuid NOT NULL,
        observed_at timestamptz NOT NULL,
        comments_count integer,
        upvote_or_score integer
    )
    """,
)

_SPECIAL_TEXT: Final = (
    ("English TITLE", "ordinary body"),
    ("ordinary title", "English BODY"),
    ("예측시장 제목", "ordinary body"),
    ("ordinary title", "한국어 본문"),
    ("Cafe\u0301 TITLE", "ordinary body"),
    ("ordinary title", "Cafe\u0301 BODY"),
    ("Astral TITLE😀", "ordinary body"),
    ("ordinary title", "Astral BODY😀"),
    ("ÉTITLE accent", "ordinary body"),
    ("éTITLE accent", "ordinary body"),
    ("ordinary title", "ÉBODY accent"),
    ("ordinary title", "éBODY accent"),
    ("ordinary title", r"literal %_\ marker"),
)


def search_database_url() -> str:
    value = os.environ.get(DATABASE_URL_ENV)
    if value is None:
        pytest.skip(f"{DATABASE_URL_ENV} is required for PostgreSQL integration")
    return value


async def seed_search_database(connection: AsyncConnection) -> None:
    for statement in _SCHEMA_SQL:
        _ = await connection.execute(text(statement))
    _ = await connection.execute(
        text(
            """
            INSERT INTO community_sources (id, display_name, country, enabled)
            VALUES (:source_id, 'Search QA', 'us', true)
            """
        ),
        {"source_id": SOURCE_ID},
    )
    rows: list[dict[str, str | UUID | datetime]] = []
    for ordinal in range(1, 56):
        entity_id = UUID(int=ordinal)
        rows.append(
            {
                "id": entity_id,
                "title": f"Indexed Market {ordinal}",
                "body": f"pagination body {ordinal}",
                "url": f"https://example.test/posts/{ordinal}",
                "published_at": NOW - timedelta(minutes=ordinal),
            }
        )
    for offset, (title, body) in enumerate(_SPECIAL_TEXT, start=100):
        entity_id = UUID(int=offset)
        rows.append(
            {
                "id": entity_id,
                "title": title,
                "body": body,
                "url": f"https://example.test/posts/{offset}",
                "published_at": NOW - timedelta(minutes=offset),
            }
        )
    _ = await connection.execute(
        text(
            """
            INSERT INTO post_versions (id, title, body)
            VALUES (:id, :title, :body)
            """
        ),
        rows,
    )
    _ = await connection.execute(
        text(
            """
            INSERT INTO posts (
                id, source_id, current_version_id, canonical_url, published_at
            )
            VALUES (:id, :source_id, :id, :url, :published_at)
            """
        ),
        tuple({**row, "source_id": SOURCE_ID} for row in rows),
    )
    _ = await connection.execute(
        text(
            """
            INSERT INTO post_matches (post_version_id, matched, normalized_phrase)
            VALUES (:post_version_id, true, 'target-rule')
            """
        ),
        tuple({"post_version_id": UUID(int=ordinal)} for ordinal in range(1, 4)),
    )
    _ = await connection.execute(text("ANALYZE post_versions"))


def search_parameters(
    search: str | None, *, keyword: str | None = None, offset: int = 0
) -> dict[str, str | UUID | datetime | int | None]:
    pattern = (
        None if search is None else search_like_pattern_v1(search_fold_v1(search).value)
    )
    return {
        "country": None,
        "source_id": None,
        "keyword": keyword,
        "search_pattern": pattern,
        "published_from": None,
        "published_to": None,
        "page_size": 100,
        "page_offset": offset,
        "current_start": NOW - timedelta(days=7),
        "current_end": NOW + timedelta(seconds=1),
        "previous_start": NOW - timedelta(days=14),
    }
