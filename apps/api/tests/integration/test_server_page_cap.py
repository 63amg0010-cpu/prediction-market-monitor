from dataclasses import replace
from uuid import UUID

import pytest
from app.collection.base import CollectionError, CollectionErrorCode
from app.collection.page_commit import ExistingPostVersion, prepare_page_commit
from app.domain.enums import TerminalReason

from .phase2_fixtures import (
    PageContextOverrides,
    PageRequestOverrides,
    accepted_post,
    oversize_post,
    page_context,
    page_request,
)


@pytest.mark.parametrize("item_kind", ["empty", "duplicate", "oversize"])
def test_server_terminalizes_exact_reviewed_page_cap(item_kind: str) -> None:
    # Given: the next empty, duplicate, or oversize-only page reaches the run cap.
    post = accepted_post()
    existing = (
        ExistingPostVersion(
            post.source_post_id,
            UUID(int=201),
            UUID(int=202),
            post.content_hash,
            1,
        ),
    )
    items = {
        "empty": (),
        "duplicate": (post,),
        "oversize": (oversize_post(),),
    }[item_kind]
    context = page_context(
        PageContextOverrides(
            existing_posts=existing if item_kind == "duplicate" else (),
            reviewed_page_cap=1,
        )
    )
    context = replace(
        context,
        run=replace(context.run, reviewed_page_cap=1, reviewed_post_cap=20),
    )
    request = page_request(
        context,
        PageRequestOverrides(terminal_reason=None, posts=items),
    )

    # When: the client submits no cap reason and the server plans the commit.
    plan = prepare_page_commit(context, request, lambda: UUID(int=301))

    # Then: page count alone seals the persisted marker exactly at the cap.
    assert plan.commit.is_terminal_page is True
    assert plan.commit.terminal_reason is TerminalReason.REVIEWED_PAGE_CAP
    assert plan.updated_run.terminal_page_commit_id == plan.commit.id
    assert plan.updated_run.completion_ready_at == context.db_now
    assert plan.outcome.response.accepted_count == 0
    assert plan.outcome.response.duplicate_count == int(item_kind == "duplicate")
    assert plan.outcome.response.rejected_count == int(item_kind == "oversize")

    sealed = replace(
        context,
        checkpoint=plan.updated_checkpoint,
        run=plan.updated_run,
    )
    later = page_request(
        sealed,
        PageRequestOverrides(
            expected_revision=1,
            expected_cursor="cursor-1",
            next_cursor="cursor-2",
            ordinal=1,
            terminal_reason=None,
        ),
    )
    with pytest.raises(CollectionError) as captured:
        _ = prepare_page_commit(sealed, later, lambda: UUID(int=302))
    assert captured.value.code is CollectionErrorCode.RUN_STREAM_SEALED
