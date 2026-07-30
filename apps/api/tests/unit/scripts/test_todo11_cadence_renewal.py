from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from apps.api.scripts.release_cadence import materialize_epoch, renew_epoch

ANCHOR = datetime(2026, 8, 1, tzinfo=UTC)
SOURCES = (
    UUID("11111111-1111-4111-8111-111111111111"),
    UUID("22222222-2222-4222-8222-222222222222"),
)
BINDING = "a" * 64
SCOPE = "b" * 64
US = timedelta(microseconds=1)


@pytest.mark.parametrize(
    "change", ["equal-recheck", "equal-expiry", "revoked", "scope", "sources"]
)
def test_only_strict_early_equivalent_renewal_preserves(change: str) -> None:
    old, slots = materialize_epoch(uuid4(), ANCHOR, SOURCES, BINDING, SCOPE)
    expires = ANCHOR + timedelta(days=31)
    recheck = ANCHOR + timedelta(days=30)
    now = ANCHOR + timedelta(days=1)
    if change == "equal-recheck":
        now = recheck
    if change == "equal-expiry":
        now, recheck = expires, expires + timedelta(days=1)
    result = renew_epoch(
        old,
        slots,
        db_now=now,
        old_expires_at=expires,
        old_recheck_at=recheck,
        new_scope_sha256="c" * 64 if change == "scope" else SCOPE,
        new_source_ids=(SOURCES[0], uuid4()) if change == "sources" else SOURCES,
        revoked=change == "revoked",
        new_epoch_id=uuid4(),
        new_anchor_at=now + timedelta(hours=3),
    )
    assert not result.preserved
    assert result.previous_epoch.invalidated_at == now
    assert result.epoch.anchor_at > now
    assert all(item.epoch_id == result.epoch.epoch_id for item in result.slots)


def test_strict_early_equivalent_renewal_preserves_identity() -> None:
    old, slots = materialize_epoch(uuid4(), ANCHOR, SOURCES, BINDING, SCOPE)
    result = renew_epoch(
        old,
        slots,
        db_now=ANCHOR + timedelta(days=30) - US,
        old_expires_at=ANCHOR + timedelta(days=31),
        old_recheck_at=ANCHOR + timedelta(days=30),
        new_scope_sha256=SCOPE,
        new_source_ids=SOURCES,
        revoked=False,
    )
    assert result.preserved
    assert result.epoch is old
    assert result.slots is slots
