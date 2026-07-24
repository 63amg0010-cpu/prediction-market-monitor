"""Codex runner sealed behind an approved capability permit."""

from dataclasses import dataclass
from typing import override

from .capability import CapabilityApproved


@dataclass(frozen=True, slots=True)
class IsolationUnavailableError(Exception):
    """Local runner refusal for unsupported compound isolation."""

    reason_codes: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return ",".join(self.reason_codes)


class UnsupportedWindowsCodexRunner:
    """Refuse because zero-tool, zero-read, and zero-network are unproven."""

    def run(self, permit: CapabilityApproved, content: str) -> bytes:
        """Require a permit in the type signature and still fail on this host."""
        del permit, content
        raise IsolationUnavailableError(
            (
                "zero_tools_unproven",
                "zero_network_boundary_unproven",
                "zero_filesystem_read_unproven",
            )
        )
