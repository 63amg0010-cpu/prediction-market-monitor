from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from app.collection.analysis_input_store import AnalysisQueueVersions
from app.collection.page_commit import prepare_page_commit
from app.collection.post_store import PageWriteContext, persist_page_items
from app.db.post_models import Post, PostVersion
from tests.integration.phase2_fixtures import (
    PageRequestOverrides,
    accepted_post,
    page_context,
    page_request,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 7, 26, 12, 50, tzinfo=UTC)
POST_ID = UUID("11111111-1111-4111-8111-111111111111")
VERSION_ID = UUID("22222222-2222-4222-8222-222222222222")
SOURCE_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
COMMIT_ID = UUID("55555555-5555-4555-8555-555555555555")


class _Rows:
    def __iter__(self) -> object:
        return iter(())


class _Result:
    def scalars(self) -> _Rows:
        return _Rows()


class _Session:
    def __init__(self) -> None:
        self.events: list[type[object] | str] = []

    async def get(
        self, _model: type[object], _identity: object, **_kwargs: object
    ) -> None:
        return None

    def add(self, value: object) -> None:
        self.events.append(type(value))

    async def flush(self) -> None:
        self.events.append("flush")

    async def execute(self, _statement: object) -> _Result:
        return _Result()


@pytest.mark.asyncio
async def test_new_post_flushes_identity_then_version_before_current_pointer() -> None:
    context = page_context()
    request = page_request(
        context,
        PageRequestOverrides(posts=(accepted_post(),)),
    )
    new_ids = iter((POST_ID, VERSION_ID, COMMIT_ID))
    plan = prepare_page_commit(context, request, lambda: next(new_ids))
    write_context = PageWriteContext(
        run_id=RUN_ID,
        source_id=SOURCE_ID,
        observed_at=NOW,
        versions=AnalysisQueueVersions("prompt-v1", "model-v1", "schema-v1"),
    )
    session = _Session()

    await persist_page_items(
        cast("AsyncSession", cast("object", session)),
        write_context,
        plan,
    )

    assert session.events[:4] == [Post, "flush", PostVersion, "flush"]
