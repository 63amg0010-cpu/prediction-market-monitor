"""Authorized source adapter construction for the collector command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, RootModel, SecretStr

from app.domain.enums import SourcePlatform

from .adapters.dcinside import (
    DCInsideAdapter,
    DCInsideFetchRequest,
    create_dcinside_http_client,
)
from .adapters.manifold import (
    ManifoldAdapter,
    ManifoldFetchRequest,
    create_manifold_http_client,
)
from .adapters.models import (
    AdapterPage,
    BlockedFetchRequest,
    PreflightContext,
    PreflightResult,
    SourceAuthorizationDecision,
)
from .adapters.naver_finance import NaverFinanceAdapter
from .adapters.reddit import (
    RedditAdapter,
    RedditFetchRequest,
    RedditOAuthCredentials,
    create_reddit_http_client,
)
from .adapters.reddit_contracts import MAX_PAGE_SIZE
from .adapters.toss_securities import TossSecuritiesAdapter
from .cli_config import CliError, required, source_ids
from .collector_workflow import PageCursor, SourceExecution

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from contextlib import AsyncExitStack
    from datetime import datetime

    from .adapters.blocked import EvidenceBlockedAdapter


class _SourceBinding(BaseModel):
    source_id: UUID
    platform: SourcePlatform | None = None
    authorization: SourceAuthorizationDecision | None = None

    def resolved_platform(self) -> SourcePlatform:
        if self.platform is not None:
            if (
                self.authorization is not None
                and self.authorization.source is not self.platform
            ):
                error_code = "source_binding_platform_mismatch"
                raise ValueError(error_code)
            return self.platform
        if self.authorization is not None:
            return self.authorization.source
        error_code = "source_binding_platform_missing"
        raise ValueError(error_code)


class _SourceBindings(RootModel[tuple[_SourceBinding, ...]]):
    pass


@dataclass(slots=True)
class _AuthorizationSlot:
    value: SourceAuthorizationDecision | None
    clock: Callable[[], datetime]
    enabled_finance_sources: frozenset[SourcePlatform]

    def context(self) -> PreflightContext:
        return PreflightContext(
            authorization=self.value,
            checked_at=self.clock(),
            enabled_finance_sources=self.enabled_finance_sources,
        )

    def bind(self, authorization: SourceAuthorizationDecision) -> None:
        self.value = authorization


async def source_executions(
    environment: Mapping[str, str],
    stack: AsyncExitStack,
    clock: Callable[[], datetime],
) -> tuple[SourceExecution, ...]:
    """Build the exact configured authorized source execution set."""
    bindings = _SourceBindings.model_validate_json(
        required(environment, "MONITOR_SOURCE_BINDINGS_JSON")
    ).root
    configured_ids = source_ids(environment)
    by_id = {binding.source_id: binding for binding in bindings}
    if len(by_id) != len(bindings) or by_id.keys() != set(configured_ids):
        error_code = "source_binding_set_mismatch"
        raise CliError(error_code)
    finance_sources = frozenset(
        binding.resolved_platform()
        for binding in bindings
        if binding.resolved_platform()
        in {SourcePlatform.NAVER_FINANCE, SourcePlatform.TOSS_SECURITIES}
    )
    executions: list[SourceExecution] = []
    for source_id in configured_ids:
        binding = by_id[source_id]
        slot = _AuthorizationSlot(
            binding.authorization,
            clock,
            finance_sources,
        )
        platform = binding.resolved_platform()
        if platform is SourcePlatform.REDDIT:
            http_client = await stack.enter_async_context(create_reddit_http_client())
            credentials = RedditOAuthCredentials(
                access_token=SecretStr(
                    required(environment, "REDDIT_OAUTH_ACCESS_TOKEN")
                ),
                user_agent=required(environment, "REDDIT_USER_AGENT"),
            )
            executions.append(
                _reddit_execution(
                    source_id,
                    RedditAdapter(http_client),
                    slot,
                    credentials,
                )
            )
            continue
        if platform is SourcePlatform.DCINSIDE:
            http_client = await stack.enter_async_context(
                create_dcinside_http_client()
            )
            executions.append(
                _dcinside_execution(
                    source_id,
                    DCInsideAdapter(http_client),
                    slot,
                    required(environment, "DCINSIDE_USER_AGENT"),
                )
            )
            continue
        if platform is SourcePlatform.MANIFOLD:
            http_client = await stack.enter_async_context(
                create_manifold_http_client()
            )
            executions.append(
                _manifold_execution(
                    source_id,
                    ManifoldAdapter(http_client),
                    slot,
                )
            )
            continue
        executions.append(
            _blocked_execution(source_id, _blocked_adapter(platform), slot)
        )
    return tuple(executions)


def _reddit_execution(
    source_id: UUID,
    adapter: RedditAdapter,
    slot: _AuthorizationSlot,
    credentials: RedditOAuthCredentials,
) -> SourceExecution:
    def preflight() -> PreflightResult:
        return adapter.preflight(slot.context())

    async def fetch_page(state: PageCursor) -> AdapterPage:
        return await adapter.fetch_page(
            RedditFetchRequest(
                preflight=slot.context(),
                credentials=credentials,
                cursor=state.cursor,
                accepted_so_far=state.accepted_count,
                page_size=MAX_PAGE_SIZE,
            )
        )

    return SourceExecution(
        source_id=source_id,
        platform=SourcePlatform.REDDIT,
        preflight=preflight,
        fetch_page=fetch_page,
        authorization=slot.value,
        bind_authorization=slot.bind,
    )


def _dcinside_execution(
    source_id: UUID,
    adapter: DCInsideAdapter,
    slot: _AuthorizationSlot,
    user_agent: str,
) -> SourceExecution:
    def preflight() -> PreflightResult:
        return adapter.preflight(slot.context())

    async def fetch_page(state: PageCursor) -> AdapterPage:
        return await adapter.fetch_page(
            DCInsideFetchRequest(
                preflight=slot.context(),
                cursor=state.cursor,
                accepted_so_far=state.accepted_count,
                page_size=20,
                user_agent=user_agent,
            )
        )

    return SourceExecution(
        source_id=source_id,
        platform=SourcePlatform.DCINSIDE,
        preflight=preflight,
        fetch_page=fetch_page,
        authorization=slot.value,
        bind_authorization=slot.bind,
    )


def _manifold_execution(
    source_id: UUID,
    adapter: ManifoldAdapter,
    slot: _AuthorizationSlot,
) -> SourceExecution:
    def preflight() -> PreflightResult:
        return adapter.preflight(slot.context())

    async def fetch_page(state: PageCursor) -> AdapterPage:
        return await adapter.fetch_page(
            ManifoldFetchRequest(
                preflight=slot.context(),
                page_ordinal=state.ordinal,
                accepted_so_far=state.accepted_count,
            )
        )

    return SourceExecution(
        source_id=source_id,
        platform=SourcePlatform.MANIFOLD,
        preflight=preflight,
        fetch_page=fetch_page,
        authorization=slot.value,
        bind_authorization=slot.bind,
    )


def _blocked_execution(
    source_id: UUID,
    adapter: EvidenceBlockedAdapter,
    slot: _AuthorizationSlot,
) -> SourceExecution:
    def preflight() -> PreflightResult:
        return adapter.preflight(slot.context())

    async def fetch_page(state: PageCursor) -> AdapterPage:
        del state
        return await adapter.fetch_page(BlockedFetchRequest(preflight=slot.context()))

    return SourceExecution(
        source_id=source_id,
        platform=adapter.source,
        preflight=preflight,
        fetch_page=fetch_page,
        authorization=slot.value,
        bind_authorization=slot.bind,
    )


def _blocked_adapter(platform: SourcePlatform) -> EvidenceBlockedAdapter:
    if platform is SourcePlatform.TOSS_SECURITIES:
        return TossSecuritiesAdapter()
    if platform is SourcePlatform.NAVER_FINANCE:
        return NaverFinanceAdapter()
    error_code = "reddit_requires_oauth_adapter"
    raise CliError(error_code)


__all__ = ("source_executions",)
