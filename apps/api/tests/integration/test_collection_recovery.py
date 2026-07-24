# ruff: noqa: INP001

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from app.collection import repository as repository_module
from app.collection.analysis_input_store import AnalysisQueueVersions
from app.collection.completion_store import CompletionServiceConfig
from app.collection.page_service_models import PageCommitServiceConfig
from app.collection.repository import (
    CollectionRepositoryConfig,
    SqlAlchemyCollectionRepository,
)
from app.collection.slot_store import MaterializationOperation
from app.db.session import DatabaseSessions

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

NEW_COMMAND_ID = UUID("2f4049ca-205a-4819-9900-68f54b968e15")
RECOVERED_COMMAND_ID = UUID("fa3d21bc-3327-4de7-97d5-f5ddb959f682")
NOW = datetime(2026, 7, 22, 3, 17, tzinfo=UTC)


@pytest.mark.asyncio
async def test_materialization_returns_existing_due_work_after_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one newly materialized command and one older due command.
    events: list[str] = []

    async def materialize(
        session: AsyncSession, operation: MaterializationOperation
    ) -> tuple[UUID, ...]:
        del session, operation
        events.append("materialize")
        return (NEW_COMMAND_ID,)

    async def recover(
        session: AsyncSession, scope_version: str, retry_jitter_key: bytes
    ) -> tuple[UUID, ...]:
        del session, scope_version, retry_jitter_key
        events.append("recover")
        return (RECOVERED_COMMAND_ID, NEW_COMMAND_ID)

    monkeypatch.setattr(repository_module, "materialize_slots", materialize)
    monkeypatch.setattr(
        repository_module, "recover_due_commands", recover, raising=False
    )
    sessions = DatabaseSessions.from_environment(
        {"DATABASE_URL": "postgresql+asyncpg://monitor:test@localhost/monitor"}
    )
    repository = SqlAlchemyCollectionRepository(
        sessions,
        CollectionRepositoryConfig(
            page=PageCommitServiceConfig(
                reviewed_page_cap=4,
                reviewed_post_cap=20,
                analysis_versions=AnalysisQueueVersions(
                    prompt="prompt-v1", model="model-v1", schema="schema-v1"
                ),
            ),
            completion=CompletionServiceConfig(retry_jitter_key=b"k" * 32),
        ),
    )

    # When: the production repository performs scheduled materialization.
    try:
        command_ids = await repository.materialize(
            MaterializationOperation("scope-v1", NOW)
        )
    finally:
        await sessions.close()

    # Then: reconciliation runs in the same transaction and returns all due work.
    assert events == ["materialize", "recover"]
    assert command_ids == (RECOVERED_COMMAND_ID, NEW_COMMAND_ID)
