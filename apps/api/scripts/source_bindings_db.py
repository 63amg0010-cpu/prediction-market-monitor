"""PostgreSQL advisory lock and durable source-binding journal."""

# ruff: noqa: ANN201, D103

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from scripts.activation_migration_state import REPOSITORY_UUID_NAMESPACE
from scripts.source_bindings_contracts import (
    ADVISORY_LOCK_SQL,
    ADVISORY_UNLOCK_SQL,
    MANIFOLD_SOURCE_ID,
    OPTIONAL_STR,
    OPTIONAL_UUID,
    BindingConflictError,
    BindingPayload,
    JsonDocument,
    TransitionState,
    sha,
)


@asynccontextmanager
async def locked(database_url: str):
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            _ = await connection.execute(text(ADVISORY_LOCK_SQL))
            try:
                yield connection
            finally:
                _ = await connection.execute(text(ADVISORY_UNLOCK_SQL))
                await connection.commit()
    finally:
        await engine.dispose()


async def ensure_intent(
    connection: AsyncConnection,
    nonce: UUID,
    payload: BindingPayload,
    prestate_sha: str,
    attestation_id: UUID,
) -> UUID:
    conflict = (
        await connection.execute(
            text(
                """
                SELECT i.activation_nonce, i.payload_sha256
                FROM source_binding_change_intents i
                JOIN LATERAL (
                    SELECT state FROM source_activation_state_transitions
                    WHERE binding_intent_id = i.id
                    ORDER BY transition_at_db DESC, id DESC LIMIT 1
                ) latest ON true
                WHERE i.activation_nonce <> :nonce
                  AND latest.state NOT IN ('active', 'restored', 'failed')
                LIMIT 1
                """
            ),
            {"nonce": nonce},
        )
    ).first()
    if conflict is not None:
        raise BindingConflictError
    intent_id = uuid5(
        REPOSITORY_UUID_NAMESPACE,
        f"binding-intent:{nonce}:{payload.sha256}",
    )
    _ = await connection.execute(
        text(
            """
            INSERT INTO source_binding_change_intents (
                id, activation_nonce, source_id, attestation_id,
                payload_sha256, prestate_sha256, scope_version
            ) VALUES (
                :id, :nonce, :source_id, :attestation_id,
                :payload_sha, :prestate_sha, :scope
            ) ON CONFLICT (activation_nonce) DO NOTHING
            """
        ),
        {
            "attestation_id": attestation_id,
            "id": intent_id,
            "nonce": nonce,
            "payload_sha": payload.sha256,
            "prestate_sha": prestate_sha,
            "scope": payload.scope_version,
            "source_id": MANIFOLD_SOURCE_ID,
        },
    )
    row = (
        await connection.execute(
            text(
                """
                SELECT id, payload_sha256, prestate_sha256, scope_version,
                       attestation_id
                FROM source_binding_change_intents
                WHERE activation_nonce = :nonce
                """
            ),
            {"nonce": nonce},
        )
    ).one()
    if tuple(row) != (
        intent_id,
        payload.sha256,
        prestate_sha,
        payload.scope_version,
        attestation_id,
    ):
        raise BindingConflictError
    await connection.commit()
    return intent_id


async def append_transition(
    connection: AsyncConnection,
    nonce: UUID,
    intent_id: UUID,
    attestation_id: UUID,
    state: TransitionState,
) -> None:
    latest_raw = (
        await connection.execute(
            text(
                """
                SELECT id FROM source_activation_state_transitions
                WHERE activation_nonce = :nonce
                ORDER BY transition_at_db DESC, id DESC LIMIT 1
                """
            ),
            {"nonce": nonce},
        )
    ).scalar_one_or_none()
    latest: UUID | None = OPTIONAL_UUID.validate_python(latest_raw)
    receipt: JsonDocument = {
        "activation_nonce": str(nonce),
        "binding_intent_id": str(intent_id),
        "predecessor_transition_id": str(latest) if latest else None,
        "state": state,
    }
    receipt_sha = sha(receipt)
    transition_id = uuid5(
        REPOSITORY_UUID_NAMESPACE,
        f"transition:{nonce}:{state}:{receipt_sha}",
    )
    _ = await connection.execute(
        text(
            """
            INSERT INTO source_activation_state_transitions (
                id, activation_nonce, source_id, attestation_id,
                binding_intent_id, predecessor_transition_id, state,
                receipt_sha256
            ) VALUES (
                :id, :nonce, :source_id, :attestation_id,
                :intent_id, :predecessor_id, :state, :receipt_sha
            ) ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING
            """
        ),
        {
            "attestation_id": attestation_id,
            "id": transition_id,
            "intent_id": intent_id,
            "nonce": nonce,
            "predecessor_id": latest,
            "receipt_sha": receipt_sha,
            "source_id": MANIFOLD_SOURCE_ID,
            "state": state,
        },
    )
    await connection.commit()


async def latest_state(
    connection: AsyncConnection,
    nonce: UUID,
) -> str | None:
    value = (
        await connection.execute(
            text(
                """
                SELECT state FROM source_activation_state_transitions
                WHERE activation_nonce = :nonce
                ORDER BY transition_at_db DESC, id DESC LIMIT 1
                """
            ),
            {"nonce": nonce},
        )
    ).scalar_one_or_none()
    return OPTIONAL_STR.validate_python(value)


__all__ = ("append_transition", "ensure_intent", "latest_state", "locked")
