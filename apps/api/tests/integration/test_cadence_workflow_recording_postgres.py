"""Real PostgreSQL cadence failure, retry, and HOLD semantics."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from .cadence_workflow_postgres_support import (
    DATABASE_ENV,
    UNSAFE_SLOT_KEY,
    RequestSpec,
    provision,
    request,
)


@pytest.mark.asyncio
async def test_real_postgres_failed_then_timely_retry_cas_and_idempotency() -> None:
    url = os.environ.get(DATABASE_ENV)
    if not url:
        pytest.skip(f"{DATABASE_ENV} is not configured")
    fixture = await provision(url)
    run_base = uuid4().int % 8_000_000_000_000_000_000
    failed = await fixture.recorder.record(
        SecretStr("token"),
        request(
            fixture,
            RequestSpec(run_base, 1, None, (True, False)),
        ),
    )
    assert not failed.cadence_accepted
    assert (failed.reason, failed.retry_permitted) == ("source_failed", True)

    retry_request = request(
        fixture,
        RequestSpec(run_base + 1, 2, failed.attempt_id, (True, True)),
    )
    accepted = await fixture.recorder.record(SecretStr("token"), retry_request)
    replay = await fixture.recorder.record(SecretStr("token"), retry_request)
    assert accepted.cadence_accepted
    assert replay == accepted

    duplicate = await fixture.recorder.record(
        SecretStr("token"),
        request(
            fixture,
            RequestSpec(run_base + 2, 2, failed.attempt_id, (True, True)),
        ),
    )
    assert (duplicate.cadence_accepted, duplicate.reason) == (
        False,
        "duplicate_after_acceptance",
    )

    unsafe = await fixture.recorder.record(
        SecretStr("token"),
        request(
            fixture,
            RequestSpec(
                run_base + 3,
                1,
                None,
                (True, False),
                UNSAFE_SLOT_KEY,
                safe_failure=False,
            ),
        ),
    )
    assert (unsafe.cadence_accepted, unsafe.reason, unsafe.retry_permitted) == (
        False,
        "source_failed",
        False,
    )
    illegal_retry = await fixture.recorder.record(
        SecretStr("token"),
        request(
            fixture,
            RequestSpec(
                run_base + 4,
                2,
                unsafe.attempt_id,
                (True, True),
                UNSAFE_SLOT_KEY,
            ),
        ),
    )
    assert (
        illegal_retry.cadence_accepted,
        illegal_retry.reason,
        illegal_retry.retry_permitted,
    ) == (False, "retry_proof_invalid", False)
    async with fixture.sessions.open() as session:
        persisted = (
            await session.execute(
                text(
                    """
                    SELECT accepted, reason_code, retry_permitted
                    FROM cadence_workflow_attempts
                    WHERE attempt_id=:attempt_id
                    """
                ),
                {"attempt_id": unsafe.attempt_id},
            )
        ).one()
    assert persisted == (False, "source_failed", False)
    await fixture.sessions.close()
