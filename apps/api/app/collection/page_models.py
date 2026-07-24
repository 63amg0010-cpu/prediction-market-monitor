"""Strict page boundary models and immutable page mutation facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves fields at runtime.
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Self
from uuid import UUID  # noqa: TC003 - Pydantic resolves fields at runtime.

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from app.domain.enums import TerminalReason  # noqa: TC001 - Pydantic runtime field.

from .normalizer import (  # noqa: TC001 - Pydantic resolves union fields at runtime.
    AcceptedPostInput,
    OversizePostInput,
)

if TYPE_CHECKING:
    from app.domain.enums import PageItemDisposition

    from .authorization import AuthorizationSnapshot
    from .checkpoint import CheckpointState, RunState
    from .commands import CommandState
    from .normalizer import NormalizedPageItem

LeaseToken = Annotated[
    str, StringConstraints(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_INVALID_PAGE_CONTRACT: Final = "invalid_page_contract"
_INVALID_PAGE_MESSAGE: Final = (
    "page items, terminal state, or fetch window are inconsistent"
)


class PageCommitRequest(BaseModel):
    """Strict author-free collector page request boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    command_id: UUID
    attempt: int = Field(ge=1, le=3)
    lease_token: LeaseToken
    page_idempotency_key: UUID
    expected_checkpoint_revision: int = Field(ge=0)
    expected_cursor: str | None
    next_cursor: str | None
    page_ordinal: int = Field(ge=0)
    posts: tuple[AcceptedPostInput | OversizePostInput, ...] = Field(max_length=20)
    source_page_item_count: int = Field(ge=0, le=20)
    source_page_receipt_sha256: Sha256Hex
    page_fetch_started_at: datetime
    page_fetch_finished_at: datetime
    is_terminal_page: bool
    terminal_reason: TerminalReason | None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Reject unbalanced items, invalid fetch windows, and terminal pairs."""
        terminal_pair = self.is_terminal_page == (self.terminal_reason is not None)
        valid_window = self.page_fetch_finished_at >= self.page_fetch_started_at
        if (
            self.source_page_item_count != len(self.posts)
            or not terminal_pair
            or not valid_window
        ):
            raise PydanticCustomError(
                _INVALID_PAGE_CONTRACT,
                _INVALID_PAGE_MESSAGE,
            )
        return self


@dataclass(frozen=True, slots=True)
class ExistingPostVersion:
    """Current persisted revision used for page-local deduplication."""

    source_post_id: str
    post_id: UUID
    current_version_id: UUID
    current_content_hash: str
    current_revision: int


@dataclass(frozen=True, slots=True)
class PageItemPlan:
    """Ordered item outcome and optional post-version mutation."""

    item_ordinal: int
    disposition: PageItemDisposition
    normalized_item: NormalizedPageItem
    normalized_content_hash: str
    post_id: UUID | None
    post_version_id: UUID | None
    revision: int | None


class PageCommitResponse(BaseModel):
    """Persisted byte-stable response body for first commit and replay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page_commit_id: UUID
    checkpoint_revision: int
    next_cursor: str | None
    accepted_count: int
    duplicate_count: int
    rejected_count: int
    page_content_hash: Sha256Hex


@dataclass(frozen=True, slots=True)
class PageCommitRecord:
    """Immutable committed page and server-owned chain facts."""

    id: UUID
    run_id: UUID
    command_id: UUID
    attempt: int
    lease_identity_hash: bytes
    page_idempotency_key: UUID
    page_ordinal: int
    expected_checkpoint_revision: int
    resulting_checkpoint_revision: int
    expected_cursor: str | None
    next_cursor: str | None
    page_request_hash: str
    page_content_hash: str
    previous_chain_hash: str
    resulting_chain_hash: str
    source_page_receipt_sha256: str
    source_page_item_count: int
    accepted_count: int
    duplicate_count: int
    rejected_count: int
    is_terminal_page: bool
    terminal_reason: TerminalReason | None
    stored_response: bytes


@dataclass(frozen=True, slots=True)
class PageCommitOutcome:
    """HTTP-neutral page result with exact response bytes."""

    status_code: Literal[200, 201]
    response: PageCommitResponse
    response_bytes: bytes


@dataclass(frozen=True, slots=True)
class PageCommitContext:
    """Rows locked by the repository transaction before page planning."""

    db_now: datetime
    command: CommandState
    run: RunState
    checkpoint: CheckpointState
    authorization: AuthorizationSnapshot
    existing_posts: tuple[ExistingPostVersion, ...]
    existing_idempotency_commit: PageCommitRecord | None
    existing_ordinal_commit: PageCommitRecord | None
    reviewed_page_cap: int
    reviewed_post_cap: int


@dataclass(frozen=True, slots=True)
class PageCommitPlan:
    """One atomic persistence plan or a no-write stored-response replay."""

    commit: PageCommitRecord
    items: tuple[PageItemPlan, ...]
    updated_checkpoint: CheckpointState
    updated_run: RunState
    outcome: PageCommitOutcome
    should_persist: bool
