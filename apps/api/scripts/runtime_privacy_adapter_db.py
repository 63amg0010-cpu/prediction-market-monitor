"""Transactional PostgreSQL adapter for one exact privacy incident."""

# ruff: noqa: D102, D107, EM101

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import cast

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

from scripts.release_privacy_contracts import (
    ContainmentMutation,
    DatabasePurgeMutation,
    DatabaseVerification,
    FrozenTarget,
    IncidentScope,
    RestoreMutation,
)
from scripts.release_runtime_database import (
    DatabaseRuntimeError,
    engine_from_named_env,
)
from scripts.runtime_privacy_adapter import (
    PrivacyProofSession,
    PrivacyRuntimeError,
    digest,
)
from scripts.runtime_privacy_adapter_db_targets import frozen_target
from scripts.runtime_privacy_adapter_sql import (
    APPEND_DEACTIVATED,
    APPEND_RESTORED,
    DB_TIME,
    DC_FINGERPRINT,
    DISABLE,
    INVALIDATE,
    LATEST,
    LOCK,
    PURGE,
    SCOPE,
    TARGETS,
    VERIFY,
    ZERO,
)

EngineFactory = Callable[[str], AsyncEngine]


class PrivacyDatabaseAdapter:
    """Concrete async SQLAlchemy implementation of ``PrivacyDatabase``."""

    def __init__(self, engine: AsyncEngine, proof: PrivacyProofSession) -> None:
        self._engine: AsyncEngine = engine
        self._proof: PrivacyProofSession = proof

    @classmethod
    def from_env(
        cls,
        env_name: str,
        proof: PrivacyProofSession,
        *,
        environ: Mapping[str, str] | None = None,
        factory: EngineFactory = create_async_engine,
    ) -> PrivacyDatabaseAdapter:
        try:
            engine = engine_from_named_env(
                env_name,
                environ=environ,
                factory=factory,
            )
        except DatabaseRuntimeError as error:
            raise PrivacyRuntimeError(str(error)) from error
        return cls(engine, proof)

    @staticmethod
    def _params(scope: IncidentScope) -> dict[str, object]:
        return {
            "source_id": scope.source_id,
            "epoch_id": scope.epoch_id,
            "activation_nonce": scope.activation_nonce,
        }

    async def _guard(
        self, connection: AsyncConnection, scope: IncidentScope
    ) -> tuple[object, str]:
        params = self._params(scope)
        _ = await connection.execute(LOCK, params)
        scoped_raw = (await connection.execute(SCOPE, params)).mappings().one_or_none()
        latest = (await connection.execute(LATEST, params)).one_or_none()
        if scoped_raw is None or latest is None:
            raise PrivacyRuntimeError("database_scope_mismatch")
        scoped = cast("Mapping[str, object]", scoped_raw)
        transition_id = cast("object", latest[0])
        state_value = cast("object", latest[1])
        if scoped.get("platform") != "manifold":
            raise PrivacyRuntimeError("database_scope_mismatch")
        return transition_id, str(state_value)

    async def frozen_targets(self, scope: IncidentScope) -> tuple[FrozenTarget, ...]:
        async with self._engine.begin() as connection:
            _ = await self._guard(connection, scope)
            rows = (await connection.execute(TARGETS, self._params(scope))).mappings()
            return tuple(
                frozen_target(cast("Mapping[str, object]", row)) for row in rows
            )

    async def contain(self, scope: IncidentScope) -> ContainmentMutation:
        async with self._engine.begin() as connection:
            transition_id, state = await self._guard(connection, scope)
            if state not in {"active", "deactivated"}:
                raise PrivacyRuntimeError("contain_state_invalid")
            observed_raw = cast("object", await connection.scalar(DB_TIME))
            rows = (await connection.execute(TARGETS, self._params(scope))).mappings()
            targets = tuple(
                frozen_target(cast("Mapping[str, object]", row)) for row in rows
            )
            mutation_sha = digest(
                (
                    "contain",
                    observed_raw,
                    [item.model_dump(mode="json") for item in targets],
                )
            )
            if (await connection.execute(DISABLE, self._params(scope))).rowcount != 1:
                raise PrivacyRuntimeError("contain_source_cas_failed")
            _ = await connection.execute(INVALIDATE, self._params(scope))
            result = await connection.execute(
                APPEND_DEACTIVATED,
                {"transition_id": transition_id, "receipt_sha256": mutation_sha},
            )
            if result.rowcount != 1 or not isinstance(observed_raw, datetime):
                raise PrivacyRuntimeError("contain_transition_cas_failed")
            return ContainmentMutation(
                observed_at=observed_raw,
                source_disabled=True,
                current_pointers_cleared=True,
                reads_blocked=True,
                state="deactivated",
                frozen_targets=targets,
                mutation_sha256=mutation_sha,
            )

    async def purge(
        self, scope: IncidentScope, containment_sha256: str
    ) -> DatabasePurgeMutation:
        async with self._engine.begin() as connection:
            _, state = await self._guard(connection, scope)
            if state not in {"deactivated", "restore_writing"}:
                raise PrivacyRuntimeError("purge_state_invalid")
            before = tuple((await connection.execute(DC_FINGERPRINT)).one())
            observed_raw = cast("object", await connection.scalar(DB_TIME))
            deleted_raw = cast(
                "object",
                (await connection.execute(PURGE, self._params(scope))).scalar_one(),
            )
            zero_raw = (
                (await connection.execute(ZERO, self._params(scope))).mappings().one()
            )
            zero = cast("Mapping[str, object]", zero_raw)
            after = tuple((await connection.execute(DC_FINGERPRINT)).one())
            clean = zero["content_count"] == zero["title_body_url_hash_count"] == 0
            if (
                not clean
                or before != after
                or not isinstance(observed_raw, datetime)
                or not isinstance(deleted_raw, int)
            ):
                raise PrivacyRuntimeError("database_purge_incomplete")
            mutation_sha = digest(
                ("purge", containment_sha256, deleted_raw, observed_raw)
            )
            return DatabasePurgeMutation(
                observed_at=observed_raw,
                affected_content_deleted=True,
                zero_title_body_url_hashes=True,
                dcinside_intact=True,
                deleted_row_count=deleted_raw,
                mutation_sha256=mutation_sha,
            )

    async def verify(self, scope: IncidentScope) -> DatabaseVerification:
        async with self._engine.begin() as connection:
            _ = await self._guard(connection, scope)
            row_raw = (
                (await connection.execute(VERIFY, self._params(scope))).mappings().one()
            )
            row = cast("Mapping[str, object]", row_raw)
        proof_sha = digest(dict(row))
        accepted = all(
            bool(row[key])
            for key in (
                "content_zero",
                "search_zero",
                "disabled",
                "cleared",
                "dcinside_intact",
            )
        ) and (
            row["revision"] == "20260727_0010"
            and row["latest_state"] == "restore_writing"
        )
        if accepted:
            self._proof.record("database", scope, proof_sha, accepted=True)
        return DatabaseVerification(
            database_content_zero=bool(row["content_zero"]),
            database_search_zero=bool(row["search_zero"]),
            dcinside_intact=bool(row["dcinside_intact"]),
            source_disabled=bool(row["disabled"]),
            current_pointers_cleared=bool(row["cleared"]),
            revision=str(row["revision"]),
            latest_state=str(row["latest_state"]),
            verification_sha256=proof_sha,
        )

    async def append_restored(
        self, scope: IncidentScope, purge_sha256: str, matrix_b_sha256: str
    ) -> RestoreMutation:
        proofs = self._proof.require_complete(scope)
        async with self._engine.begin() as connection:
            transition_id, state = await self._guard(connection, scope)
            if state != "restore_writing":
                raise PrivacyRuntimeError("restore_state_cas_failed")
            observed_raw = cast("object", await connection.scalar(DB_TIME))
            mutation_sha = digest(
                (
                    "privacy-restore",
                    purge_sha256,
                    matrix_b_sha256,
                    proofs,
                    observed_raw,
                )
            )
            result = await connection.execute(
                APPEND_RESTORED,
                {"transition_id": transition_id, "receipt_sha256": mutation_sha},
            )
            if result.rowcount != 1 or not isinstance(observed_raw, datetime):
                raise PrivacyRuntimeError("restore_state_cas_failed")
            return RestoreMutation(
                observed_at=observed_raw,
                prior_state="restore_writing",
                state="restored",
                mutation_sha256=mutation_sha,
            )

    async def dispose(self) -> None:
        await self._engine.dispose()


__all__ = ("PrivacyDatabaseAdapter",)
