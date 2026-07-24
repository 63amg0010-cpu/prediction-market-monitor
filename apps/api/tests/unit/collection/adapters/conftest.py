from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def reddit_page_response() -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        fixture = (
            "tests_only_reddit_listing_page_2.json"
            if request.url.params.get("after")
            else "tests_only_reddit_listing_page_1.json"
        )
        return httpx2.Response(
            200,
            content=(FIXTURES / fixture).read_bytes(),
            headers={
                "content-type": "application/json",
                "x-ratelimit-used": "2",
                "x-ratelimit-remaining": "58",
                "x-ratelimit-reset": "42",
            },
        )

    return respond
