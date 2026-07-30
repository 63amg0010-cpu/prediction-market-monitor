"""Bounded, redirect-free HTTPS checks for Production observations."""

# pyright: reportImplicitOverride=false, reportUnannotatedClassAttribute=false
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# ruff: noqa: D101, D105, D107, EM101, PLR2004, TC001

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Final, Protocol, cast
from urllib.parse import urlencode, urlsplit

import httpx2

from .release_chain_common import ReleaseChainError
from .runtime_production_adapter_database import ProductionDatabaseSnapshot

MAX_BYTES: Final = 1_048_576


class HttpResponse(Protocol):
    status_code: int
    content: bytes
    headers: Mapping[str, str]


HttpGet = Callable[[str], HttpResponse]


class ProductionHttpProbe:
    """Issue only bounded HTTPS GET requests and retain no cookies or bodies."""

    def __init__(
        self,
        *,
        get: HttpGet | None = None,
        timeout_seconds: float = 10,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        if timeout_seconds <= 0 or max_bytes <= 0:
            raise ReleaseChainError("production_http_policy_invalid")
        self._get = get
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    def verify(
        self,
        *,
        api_url: str,
        web_url: str,
        snapshot: ProductionDatabaseSnapshot,
    ) -> None:
        """Require HTTP count, identity, filter, and freshness parity with SQL."""
        health = self._json(_url(api_url, "/v1/health"))
        if health.get("status") != "ok" or health.get("db") != "ok":
            raise ReleaseChainError("production_api_health_failed")
        login = self._bytes(_url(web_url, "/login"), accept="text/html")
        if b"<html" not in login.lower():
            raise ReleaseChainError("production_web_health_failed")
        common = {"source_id": snapshot.source_id, "page": 1, "page_size": 50}
        positive_url = _url(
            api_url, "/v1/posts", {**common, "search": snapshot.literal}
        )
        positive = self._post_page(positive_url)
        repeated = self._post_page(positive_url)
        self._parity(positive, snapshot.search.positive_total, snapshot.page_ids)
        self._parity(repeated, snapshot.search.positive_total, snapshot.page_ids)
        negative = self._post_page(
            _url(
                api_url,
                "/v1/posts",
                {**common, "search": snapshot.negative_literal},
            )
        )
        self._parity(negative, 0, ())
        keyword = self._post_page(
            _url(api_url, "/v1/posts", {**common, "keyword": snapshot.keyword})
        )
        self._total(keyword, snapshot.search.keyword_total)
        combined = self._post_page(
            _url(
                api_url,
                "/v1/posts",
                {
                    **common,
                    "keyword": snapshot.keyword,
                    "search": snapshot.literal,
                },
            )
        )
        self._total(combined, snapshot.search.and_total)
        dashboard = self._json(
            _url(api_url, "/v1/dashboard", {"source_id": snapshot.source_id})
        )
        sources = dashboard.get("sources")
        if not isinstance(sources, list) or not any(
            isinstance(item, dict)
            and item.get("source_id") == snapshot.source_id
            and item.get("enabled") is True
            and isinstance(item.get("latest_successful_run_at"), str)
            for item in cast("list[object]", sources)
        ):
            raise ReleaseChainError("production_source_freshness_failed")

    def _post_page(self, url: str) -> Mapping[str, object]:
        value = self._json(url)
        page = value.get("page")
        items = value.get("items")
        if not isinstance(page, dict) or not isinstance(items, list):
            raise ReleaseChainError("production_http_schema_invalid")
        return value

    def _parity(
        self,
        value: Mapping[str, object],
        expected_total: int,
        expected_ids: tuple[str, ...],
    ) -> None:
        self._total(value, expected_total)
        items = cast("list[object]", value["items"])
        actual = tuple(str(item.get("id")) for item in items if isinstance(item, dict))
        if actual != expected_ids:
            raise ReleaseChainError("production_http_page_mismatch")

    @staticmethod
    def _total(value: Mapping[str, object], expected: int) -> None:
        page = cast("dict[object, object]", value["page"])
        if page.get("total_items") != expected:
            raise ReleaseChainError("production_http_total_mismatch")

    def _json(self, url: str) -> Mapping[str, object]:
        raw = self._bytes(url, accept="application/json")
        try:
            value = cast("object", json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseChainError("production_http_json_invalid") from error
        if not isinstance(value, dict):
            raise ReleaseChainError("production_http_schema_invalid")
        return cast("Mapping[str, object]", value)

    def _bytes(self, url: str, *, accept: str) -> bytes:
        _require_https(url)
        try:
            if self._get is not None:
                response = self._get(url)
            else:
                with httpx2.Client(
                    follow_redirects=False,
                    timeout=self._timeout,
                    headers={"accept": accept, "cache-control": "no-store"},
                ) as client:
                    response = client.get(url)
        except (httpx2.HTTPError, OSError, TimeoutError) as error:
            raise ReleaseChainError("production_http_failed") from error
        if 300 <= response.status_code < 400:
            raise ReleaseChainError("production_http_redirect_forbidden")
        if response.status_code != 200:
            raise ReleaseChainError("production_http_status_rejected")
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    raise ReleaseChainError("production_http_body_too_large")
            except ValueError as error:
                raise ReleaseChainError("production_http_length_invalid") from error
        if not response.content or len(response.content) > self._max_bytes:
            raise ReleaseChainError("production_http_body_size_invalid")
        return bytes(response.content)

    def __repr__(self) -> str:
        return "ProductionHttpProbe(redacted=True)"


def _url(
    base: str,
    path: str,
    parameters: Mapping[str, object] | None = None,
) -> str:
    result = f"{base.rstrip('/')}{path}"
    if parameters:
        result = f"{result}?{urlencode(parameters)}"
    _require_https(result)
    return result


def _require_https(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ReleaseChainError("production_http_url_invalid")


__all__ = ("HttpGet", "HttpResponse", "ProductionHttpProbe")
