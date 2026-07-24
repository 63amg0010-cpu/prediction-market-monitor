import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.db.auth_models import CommunitySource
from app.db.post_models import Post, PostVersion
from app.db.report_models import DailyReportVersion
from app.db.session import DatabaseSessions
from app.domain.enums import Country, PostVersionReason, SourcePlatform
from app.reporting.reconciliation import ReconcileRequest, reconcile_report
from app.reporting.repository import SqlAlchemyReportRepository
from app.reporting.reproduction import ReproducedReport, reproduce_report
from app.reporting.retention import cleanup_source
from app.reporting.retention_sql import SqlAlchemyRetentionRepository
from app.reporting.retention_types import CleanupRequest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from tests.unit.reporting.factories import digest, manifest_payload

DATABASE_URL_ENV = "RP07_DATABASE_URL"
REPORT_ID = UUID(int=7100)
VERSION_ID = UUID(int=7101)
MANIFEST_ID = UUID(int=7102)
SOURCE_ID = UUID(int=7110)
POST_ID = UUID(int=7111)
POST_VERSION_ID = UUID(int=7112)
READER_ROLE = "rp07_retained_reader"
CREATED_AT = datetime(2026, 7, 23, tzinfo=UTC)
RAW_COLLECTED_AT = datetime(2026, 5, 1, tzinfo=UTC)
RETENTION_OBSERVED_AT = datetime(2026, 7, 23, tzinfo=UTC)


def _database_url() -> str:
    database_url = os.environ.get(DATABASE_URL_ENV)
    if database_url is None:
        pytest.skip(f"{DATABASE_URL_ENV} is required for real PostgreSQL proof")
    return database_url


async def _insert_eligible_raw_post(sessions: DatabaseSessions) -> None:
    async with sessions.open() as session, session.begin():
        session.add(
            CommunitySource(
                id=SOURCE_ID,
                country=Country.US,
                platform=SourcePlatform.REDDIT,
                external_key="rp07-proof",
                display_name="RP-07 proof source",
                scope_version="rp07-v1",
                enabled=False,
                active_authorization_id=None,
                created_at=CREATED_AT,
            )
        )
        session.add(
            Post(
                id=POST_ID,
                source_id=SOURCE_ID,
                source_post_id="rp07-post",
                canonical_url="https://example.invalid/rp07-post",
                published_at=RAW_COLLECTED_AT,
                language="en",
                current_version_id=None,
                created_at=RAW_COLLECTED_AT,
                updated_at=RAW_COLLECTED_AT,
            )
        )
    async with sessions.open() as session, session.begin():
        session.add(
            PostVersion(
                id=POST_VERSION_ID,
                post_id=POST_ID,
                revision=1,
                content_hash=digest(7112),
                title="retention proof",
                body="eligible raw payload",
                body_bytes=20,
                reason=PostVersionReason.FIRST_SEEN,
                collected_at=RAW_COLLECTED_AT,
            )
        )
        post = await session.get(Post, POST_ID)
        assert post is not None
        post.current_version_id = POST_VERSION_ID


async def _create_forbidden_query_reader(
    sessions: DatabaseSessions,
    database_url: str,
) -> tuple[DatabaseSessions, bool]:
    async with sessions.open() as session, session.begin():
        _ = await session.execute(
            text(
                f"""DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT FROM pg_roles WHERE rolname = '{READER_ROLE}'
                ) THEN
                    CREATE ROLE {READER_ROLE} LOGIN;
                END IF;
                END $$"""
            )
        )
        _ = await session.execute(
            text(f"GRANT USAGE ON SCHEMA public TO {READER_ROLE}")
        )
        _ = await session.execute(
            text(
                f"""GRANT SELECT ON
                report_input_manifests, daily_report_versions
                TO {READER_ROLE}"""
            )
        )
        raw_select_forbidden = not bool(
            await session.scalar(
                select(
                    func.has_table_privilege(
                        READER_ROLE,
                        "post_versions",
                        "SELECT",
                    )
                )
            )
        )
    reader_url = make_url(database_url).set(username=READER_ROLE, password=None)
    return DatabaseSessions.from_secret(SecretStr(reader_url.render_as_string())), (
        raw_select_forbidden
    )


@pytest.mark.asyncio
async def test_retained_report_survives_real_postgresql_raw_post_purge() -> None:
    # Given: a migrated PostgreSQL database and a report with retained-only inputs.
    database_url = _database_url()
    sessions = DatabaseSessions.from_secret(SecretStr(database_url))
    reader_sessions: DatabaseSessions | None = None
    try:
        payload = manifest_payload(()).model_copy(update={"source_coverage": ()})
        outcome = await reconcile_report(
            SqlAlchemyReportRepository(sessions),
            ReconcileRequest(
                payload=payload,
                created_at=CREATED_AT,
                report_id=REPORT_ID,
                version_id=VERSION_ID,
                manifest_id=MANIFEST_ID,
            ),
        )
        assert outcome.created is True
        assert outcome.version.revision == 1

        # When: RESTRICT blocks the selected version, then eligible raw rows purge.
        async with sessions.open() as session:
            with pytest.raises(IntegrityError):
                _ = await session.execute(
                    delete(DailyReportVersion).where(
                        DailyReportVersion.id == VERSION_ID
                    )
                )
            await session.rollback()
        await _insert_eligible_raw_post(sessions)
        cleanup = await cleanup_source(
            SqlAlchemyRetentionRepository(sessions),
            CleanupRequest(
                source_entity_id=POST_VERSION_ID,
                observed_at=RETENTION_OBSERVED_AT,
            ),
        )
        assert cleanup.deleted is True
        async with sessions.open() as session, session.begin():
            post_count = await session.scalar(
                select(func.count()).select_from(Post).where(Post.id == POST_ID)
            )
            version_count = await session.scalar(
                select(func.count())
                .select_from(PostVersion)
                .where(PostVersion.id == POST_VERSION_ID)
            )
        assert (post_count, version_count) == (0, 0)

        # Then: a DB role forbidden from raw SELECT can load and reproduce the report.
        reader_sessions, raw_select_forbidden = await _create_forbidden_query_reader(
            sessions,
            database_url,
        )
        assert raw_select_forbidden is True
        retained = await SqlAlchemyReportRepository(reader_sessions).load_retained(
            MANIFEST_ID
        )
        assert retained is not None
        reproduced = reproduce_report(retained)
        assert isinstance(reproduced, ReproducedReport)
        assert (
            reproduced.report_payload_sha256
            == outcome.version.retained.report_payload_sha256
        )
    finally:
        if reader_sessions is not None:
            await reader_sessions.close()
        await sessions.close()
