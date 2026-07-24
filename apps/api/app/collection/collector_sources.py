"""Authorized source adapter construction for the collector command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, RootModel, SecretStr

from app.domain.enums import SourcePlatform

from .adapters.dcinside import DCInsideAdapter
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
    from uuid import UUID

    from .adapters.blocked import EvidenceBlockedAdapter


class _SourceBinding(BaseModel):
    source_id: UUID
    authorization: SourceAuthorizationDecision


class _SourceBindings(RootModel[tuple[_SourceBinding, ...]]):
    pass


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
        binding.authorization.source
        for binding in bindings
        if binding.authorization.source
        in {SourcePlatform.NAVER_FINANCE, SourcePlatform.TOSS_SECURITIES}
    )
    executions: list[SourceExecution] = []
    for source_id in configured_ids:
        binding = by_id[source_id]
        context = PreflightContext(
            authorization=binding.authorization,
            checked_at=clock(),
            enabled_finance_sources=finance_sources,
        )
        platform = binding.authorization.source
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
                    context,
                    credentials,
                )
            )
            continue
        executions.append(
            _blocked_execution(source_id, _blocked_adapter(platform), context)
        )
    return tuple(executions)


def _reddit_execution(
    source_id: UUID,
    adapter: RedditAdapter,
    context: PreflightContext,
    credentials: RedditOAuthCredentials,
) -> SourceExecution:
    def preflight() -> PreflightResult:
        return adapter.preflight(context)

    async def fetch_page(state: PageCursor) -> AdapterPage:
        return await adapter.fetch_page(
            RedditFetchRequest(
                preflight=context,
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
        authorization=context.authorization,
    )


def _blocked_execution(
    source_id: UUID,
    adapter: EvidenceBlockedAdapter,
    context: PreflightContext,
) -> SourceExecution:
    def preflight() -> PreflightResult:
        return adapter.preflight(context)

    async def fetch_page(state: PageCursor) -> AdapterPage:
        del state
        return await adapter.fetch_page(BlockedFetchRequest(preflight=context))

    return SourceExecution(
        source_id=source_id,
        platform=adapter.source,
        preflight=preflight,
        fetch_page=fetch_page,
        authorization=context.authorization,
    )


def _blocked_adapter(platform: SourcePlatform) -> EvidenceBlockedAdapter:
    if platform is SourcePlatform.DCINSIDE:
        return DCInsideAdapter()
    if platform is SourcePlatform.TOSS_SECURITIES:
        return TossSecuritiesAdapter()
    if platform is SourcePlatform.NAVER_FINANCE:
        return NaverFinanceAdapter()
    error_code = "reddit_requires_oauth_adapter"
    raise CliError(error_code)


__all__ = ("source_executions",)
