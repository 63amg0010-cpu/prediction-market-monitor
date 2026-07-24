"""Typed failures raised while parsing reviewed configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn, override


@dataclass(frozen=True, slots=True)
class ConfigurationInvariantError(ValueError):
    """A fail-closed configuration invariant was violated."""

    code: str
    path: str
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable redacted error description."""
        return f"{self.code} at {self.path}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ConfigurationParseError(ValueError):
    """A YAML document could not be parsed at the trust boundary."""

    path: str
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable parse failure description."""
        return f"configuration parse failed for {self.path}: {self.reason}"


def invariant(code: str, path: str, reason: str) -> NoReturn:
    """Raise a structured invariant failure without a bare error string."""
    raise ConfigurationInvariantError(code, path, reason)
