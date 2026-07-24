"""Worker-side capability decision boundary."""

from dataclasses import dataclass
from typing import Literal

LOCAL_BLOCK_REASONS: tuple[str, ...] = (
    "pro_tier_unverified",
    "automation_terms_unverified",
    "zero_tools_unproven",
    "zero_network_boundary_unproven",
    "zero_filesystem_read_unproven",
    "low_privilege_token_unproven",
    "hard_resource_caps_unproven",
    "hostile_probe_blocked",
)


@dataclass(frozen=True, slots=True)
class CapabilityApproved:
    """Permit constructed only from a complete server-approved proof set."""

    proof_set_sha256: str
    status: Literal["approved"] = "approved"


@dataclass(frozen=True, slots=True)
class CapabilityBlocked:
    """Local fail-closed capability state."""

    reason_codes: tuple[str, ...]
    status: Literal["blocked_capability"] = "blocked_capability"


type CapabilityDecision = CapabilityApproved | CapabilityBlocked


def local_capability_decision() -> CapabilityBlocked:
    """Reflect the checked-in Windows proof without probing hostile input."""
    return CapabilityBlocked(reason_codes=LOCAL_BLOCK_REASONS)
