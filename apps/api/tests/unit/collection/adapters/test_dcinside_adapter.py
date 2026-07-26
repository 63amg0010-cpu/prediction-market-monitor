from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from app.collection.adapters.dcinside import (
    DCINSIDE_FIELDS,
    DCINSIDE_PURPOSE,
    DCINSIDE_ROUTES,
    DCInsideAdapter,
    DCInsideFetchRequest,
)
from app.collection.adapters.models import (
    BlockedKind,
    HttpMethod,
    NormalizedPost,
    PreflightBlocked,
    PreflightContext,
    SourceAuthorizationDecision,
    SourceBlockedError,
)
from app.domain.enums import AuthorizationStatus, SourcePlatform

NOW = datetime(2026, 7, 26, 3, 0, tzinfo=UTC)
USER_AGENT = (
    "prediction-market-monitor/1.0 "
    "(personal monitoring; github.com/63amg0010-cpu/prediction-market-monitor)"
)


def _authorization(
    *,
    routes: frozenset[str] = DCINSIDE_ROUTES,
) -> SourceAuthorizationDecision:
    return SourceAuthorizationDecision(
        decision_id=UUID("33333333-3333-4333-8333-333333333333"),
        source=SourcePlatform.DCINSIDE,
        status=AuthorizationStatus.APPROVED,
        evidence_sha256="b" * 64,
        evidence_location="docs/evidence/source-scope-register.md",
        issuer="DCInside published policy and robots.txt",
        reviewer="repository owner",
        permitted_methods=frozenset({HttpMethod.GET}),
        permitted_routes=routes,
        permitted_fields=DCINSIDE_FIELDS,
        permitted_subreddits=frozenset(),
        purpose=DCINSIDE_PURPOSE,
        requests_per_minute=30,
        concurrency=1,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        revoked_at=None,
    )


def _context(
    authorization: SourceAuthorizationDecision | None,
) -> PreflightContext:
    return PreflightContext(authorization=authorization, checked_at=NOW)


@pytest.mark.asyncio
async def test_dcinside_network_is_forbidden_without_exact_current_approval() -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(500)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        adapter = DCInsideAdapter(client)
        request = DCInsideFetchRequest(
            preflight=_context(None),
            cursor=None,
            accepted_so_far=0,
            page_size=2,
            user_agent=USER_AGENT,
        )

        with pytest.raises(SourceBlockedError):
            _ = await adapter.fetch_page(request)

    assert calls == []


def test_dcinside_preflight_rejects_any_route_broader_than_reviewed() -> None:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(500))
    )
    adapter = DCInsideAdapter(client)
    authorization = _authorization(
        routes=DCINSIDE_ROUTES | frozenset({"/mini/board/comment_view/"})
    )

    result = adapter.preflight(_context(authorization))

    assert isinstance(result, PreflightBlocked)
    assert result.kind is BlockedKind.BLOCKED_AUTHORIZATION
    assert result.code == "authorization_scope_mismatch"


@pytest.mark.asyncio
async def test_dcinside_fetches_only_reviewed_gallery_without_author_identity() -> None:
    requests: list[httpx2.Request] = []
    list_html = """
    <table>
      <tr class="ub-content us-post" data-no="31">
        <td class="gall_num">31</td>
        <td class="gall_tit ub-word">
          <a href="/mini/board/view/?id=predictionmarket&amp;no=31">
            터보플로우 프리딕션 마켓
          </a>
        </td>
      </tr>
    </table>
    """
    view_html = """
    <div class="view_content_wrap">
      <h3><span class="title_subject">터보플로우 프리딕션 마켓</span></h3>
      <div class="gall_writer" data-nick="수집하면 안 되는 작성자">
        <span class="gall_date" title="2026-06-10 11:29:45">06.10</span>
        <span class="gall_reply_num">추천 4</span>
        <span class="gall_comment"><a>댓글 2</a></span>
      </div>
      <div class="writing_view_box">
        <div class="write_div"><p>첫 번째 줄</p><p>두 번째 줄</p></div>
      </div>
    </div>
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/lists/"):
            return httpx2.Response(
                200,
                text=list_html,
                headers={"content-type": "text/html; charset=UTF-8"},
            )
        return httpx2.Response(
            200,
            text=view_html,
            headers={"content-type": "text/html; charset=UTF-8"},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        page = await DCInsideAdapter(client).fetch_page(
            DCInsideFetchRequest(
                preflight=_context(_authorization()),
                cursor=None,
                accepted_so_far=0,
                page_size=2,
                user_agent=USER_AGENT,
            )
        )

    assert len(requests) == 2
    assert all(request.url.scheme == "https" for request in requests)
    assert all(request.url.host == "gall.dcinside.com" for request in requests)
    assert all(request.headers["user-agent"] == USER_AGENT for request in requests)
    post = page.items[0]
    assert isinstance(post, NormalizedPost)
    assert post.source_post_id == "31"
    assert post.title == "터보플로우 프리딕션 마켓"
    assert post.body == "첫 번째 줄\n두 번째 줄"
    assert post.published_at == datetime(2026, 6, 10, 2, 29, 45, tzinfo=UTC)
    assert post.comments_count == 2
    assert post.upvote_or_score == 4
    assert post.canonical_url == (
        "https://gall.dcinside.com/mini/board/view/"
        "?id=predictionmarket&no=31"
    )
    serialized = post.model_dump_json()
    assert "수집하면 안 되는 작성자" not in serialized
    assert "author" not in serialized
