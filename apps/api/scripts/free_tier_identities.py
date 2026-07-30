"""Provider identity env and binding checks for free-tier captures."""

# ruff: noqa: D103, EM101, EM102, PLR2004, TRY003

from __future__ import annotations

import os

from apps.api.scripts.free_tier_capture_contract import (
    IDENTITY_BINDING_FIELDS,
    PROVIDER_IDENTITY_ENVS,
    PROVIDER_IDENTITY_ROLES,
)
from apps.api.scripts.free_tier_domain import (
    GateHoldError,
    JsonObject,
    JsonValue,
    sha256_hex,
)


def require_provider_identity_envs(
    provider: str,
    identity_envs: tuple[str, ...],
) -> None:
    if identity_envs != PROVIDER_IDENTITY_ENVS[provider]:
        raise GateHoldError("exact protected identity envs are required")


def identity_bindings(identity_envs: tuple[str, ...]) -> list[JsonObject]:
    bindings: list[JsonObject] = []
    roles = roles_for_envs(identity_envs)
    for role, name in zip(roles, identity_envs, strict=True):
        value = os.environ.get(name)
        if value is None or not value:
            raise GateHoldError(f"protected identity environment is empty: {name}")
        bindings.append({"role": role, "sha256": sha256_hex(value.encode())})
    return bindings


def roles_for_envs(identity_envs: tuple[str, ...]) -> tuple[str, ...]:
    for provider, expected in PROVIDER_IDENTITY_ENVS.items():
        if identity_envs == expected:
            return PROVIDER_IDENTITY_ROLES[provider]
    raise GateHoldError("exact protected identity envs are required")


def require_identity_bindings(capture: JsonObject, provider: str) -> None:
    bindings = capture.get("identity_bindings")
    if not isinstance(bindings, list):
        raise GateHoldError("provider identity bindings are required")
    roles = PROVIDER_IDENTITY_ROLES[provider]
    if len(bindings) != len(roles):
        raise GateHoldError("provider identity binding count mismatch")
    for role, binding in zip(roles, bindings, strict=True):
        _require_identity_binding(binding, role)


def _require_identity_binding(value: JsonValue, role: str) -> None:
    if not isinstance(value, dict) or frozenset(value) != IDENTITY_BINDING_FIELDS:
        raise GateHoldError("provider identity binding schema mismatch")
    if value.get("role") != role:
        raise GateHoldError("provider identity binding role mismatch")
    digest = value.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise GateHoldError("identity_binding.sha256 must be a lowercase SHA-256")
