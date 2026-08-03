from typing import cast

from app.collection.adapters.manifold import (
    MANIFOLD_CONCURRENCY,
    MANIFOLD_FIELDS,
    MANIFOLD_PURPOSE,
    MANIFOLD_REQUESTS_PER_MINUTE,
    MANIFOLD_ROUTES,
)
from app.services.release.source_activation import (
    _manifold_scope,  # pyright: ignore[reportPrivateUsage]
)


def test_activation_scope_exactly_matches_manifold_adapter_contract() -> None:
    scope = _manifold_scope()

    assert set(cast("list[str]", scope["permitted_methods"])) == {"GET"}
    assert set(cast("list[str]", scope["permitted_routes"])) == MANIFOLD_ROUTES
    assert set(cast("list[str]", scope["permitted_fields"])) == MANIFOLD_FIELDS
    assert scope["permitted_subreddits"] == []
    assert scope["purpose"] == MANIFOLD_PURPOSE
    assert scope["requests_per_minute"] == MANIFOLD_REQUESTS_PER_MINUTE
    assert scope["concurrency"] == MANIFOLD_CONCURRENCY
