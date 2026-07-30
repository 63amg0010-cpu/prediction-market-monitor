"""Authorized advisory-lock/CAS mutation boundaries for release recovery."""

# ruff: noqa: D102, D107
# pyright: reportAny=false, reportImplicitStringConcatenation=false
# pyright: reportUnannotatedClassAttribute=false, reportUnreachable=false

from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import TYPE_CHECKING, TypeVar
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

if TYPE_CHECKING:
    from scripts.release_privacy_contracts import IncidentScope
    from scripts.release_rollback_models import RollbackMutationIntent

T = TypeVar("T")
Mutation = Callable[[AsyncConnection], Awaitable[T]]
LOCK_ROLLBACK = text(
    "SELECT pg_advisory_xact_lock(hashtext('source-binding'), "
    "hashtext(CAST(:activation_nonce AS text)))"
)
LATEST_TRANSITION = text(
    """
    SELECT id, state
    FROM source_activation_state_transitions
    WHERE activation_nonce = :activation_nonce
    ORDER BY transition_at_db DESC, id DESC
    LIMIT 1 FOR UPDATE
    """
)
APPEND_RESTORED = text(
    """
    INSERT INTO source_activation_state_transitions (
        id, activation_nonce, source_id, attestation_id, binding_intent_id,
        predecessor_transition_id, state, receipt_sha256
    )
    SELECT gen_random_uuid(), activation_nonce, source_id, attestation_id,
           binding_intent_id, id, 'restored', :receipt_sha256
    FROM source_activation_state_transitions
    WHERE id = :transition_id AND state = 'restore_writing'
    ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING
    """
)


class MutationRuntimeError(RuntimeError):
    """Stable mutation authorization or CAS error."""


class TransactionalRollbackAdapter:
    """Commit only a validated technical rollback intent."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def finalize(self, intent: RollbackMutationIntent) -> None:
        if (
            intent.incident_class != "technical"
            or intent.next_transition != "restored"
            or intent.expected_latest_transition != "restore_writing"
        ):
            msg = "rollback_intent_unauthorized"
            raise MutationRuntimeError(msg)
        async with self._engine.begin() as connection:
            _ = await connection.execute(
                LOCK_ROLLBACK,
                {"activation_nonce": intent.activation_nonce},
            )
            row = (
                await connection.execute(
                    LATEST_TRANSITION,
                    {"activation_nonce": intent.activation_nonce},
                )
            ).one_or_none()
            actual_id = None if row is None else row[0]
            actual_integer = (
                actual_id.int if isinstance(actual_id, UUID) else actual_id
            )
            if (
                row is None
                or actual_integer != intent.expected_latest_transition_id
                or row[1] != "restore_writing"
            ):
                msg = "rollback_transition_cas_failed"
                raise MutationRuntimeError(msg)
            result = await connection.execute(
                APPEND_RESTORED,
                {
                    "receipt_sha256": str(
                        intent.receipt_body["receipt_sha256"]
                    ),
                    "transition_id": actual_id,
                },
            )
            if result.rowcount != 1:
                msg = "rollback_transition_cas_failed"
                raise MutationRuntimeError(msg)


class AuthorizedPrivacyTransactions:
    """Own privacy DB transactions while injected operations own SQL details."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def contain(self, scope: IncidentScope, mutation: Mutation[T]) -> T:
        return await self._run(scope, mutation, terminal=False)

    async def purge(self, scope: IncidentScope, mutation: Mutation[T]) -> T:
        return await self._run(scope, mutation, terminal=False)

    async def verify_and_restore(
        self,
        scope: IncidentScope,
        mutation: Mutation[T],
    ) -> T:
        return await self._run(scope, mutation, terminal=True)

    async def _run(
        self,
        scope: IncidentScope,
        mutation: Mutation[T],
        *,
        terminal: bool,
    ) -> T:
        if scope.violation_kind not in {"privacy", "authorization"}:
            msg = "privacy_scope_unauthorized"
            raise MutationRuntimeError(msg)
        async with self._engine.begin() as connection:
            _ = await connection.execute(
                LOCK_ROLLBACK,
                {"activation_nonce": scope.activation_nonce},
            )
            row = (
                await connection.execute(
                    LATEST_TRANSITION,
                    {"activation_nonce": scope.activation_nonce},
                )
            ).one_or_none()
            if row is None:
                msg = "privacy_transition_missing"
                raise MutationRuntimeError(msg)
            state = str(row[1])
            allowed = {"active", "deactivated", "restore_writing"}
            if state not in allowed:
                msg = "privacy_transition_unauthorized"
                raise MutationRuntimeError(msg)
            if terminal and state != "restore_writing":
                msg = "privacy_restore_unauthorized"
                raise MutationRuntimeError(msg)
            return await mutation(connection)


def mutation_digest(*values: str) -> str:
    """Build a public-safe mutation digest from already validated identifiers."""
    return sha256("\0".join(values).encode()).hexdigest()


__all__ = (
    "AuthorizedPrivacyTransactions",
    "MutationRuntimeError",
    "TransactionalRollbackAdapter",
    "mutation_digest",
)
