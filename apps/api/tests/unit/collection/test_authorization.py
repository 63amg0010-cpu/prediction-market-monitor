from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.collection.authorization import (
    AuthorizationSnapshot,
    require_active_authorization,
)
from app.collection.base import CollectionError, CollectionErrorCode
from app.domain.enums import AuthorizationStatus


def test_active_authorization_is_source_and_scope_bound() -> None:
    # Given: an effective, unrevoked approval for one exact source scope.
    now = datetime(2026, 7, 20, tzinfo=UTC)
    source_id = uuid4()
    decision = AuthorizationSnapshot(
        decision_id=uuid4(),
        source_id=source_id,
        scope_version="scope-v1",
        enabled=True,
        status=AuthorizationStatus.APPROVED,
        effective_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=1),
        revoked_at=None,
    )

    # When: the page path checks the bound approval.
    active = require_active_authorization(decision, source_id, "scope-v1", now)

    # Then: the immutable decision identity is returned.
    assert active.decision_id == decision.decision_id


@pytest.mark.parametrize("change", ["disabled", "expired", "revoked", "scope"])
def test_inactive_authorization_fails_closed(change: str) -> None:
    # Given: an authorization with one invalidating condition.
    now = datetime(2026, 7, 20, tzinfo=UTC)
    source_id = uuid4()
    decision = AuthorizationSnapshot(
        decision_id=uuid4(),
        source_id=source_id,
        scope_version="other" if change == "scope" else "scope-v1",
        enabled=change != "disabled",
        status=AuthorizationStatus.APPROVED,
        effective_at=now - timedelta(days=1),
        expires_at=now - timedelta(seconds=1)
        if change == "expired"
        else now + timedelta(days=1),
        revoked_at=now if change == "revoked" else None,
    )

    # When/Then: the gate rejects instead of treating configuration as proof.
    with pytest.raises(CollectionError) as captured:
        _ = require_active_authorization(decision, source_id, "scope-v1", now)
    assert captured.value.code is CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE
