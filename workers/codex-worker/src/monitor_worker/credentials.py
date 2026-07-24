"""Windows credential-manager port."""

from enum import StrEnum, unique
from typing import Protocol

from pydantic import SecretBytes


@unique
class CredentialTarget(StrEnum):
    """Credential Manager targets used by the worker parent only."""

    WORKER_BOOTSTRAP = "PredictionMarketMonitor/worker-bootstrap"


class CredentialManager(Protocol):
    """Read a bootstrap secret from Windows Credential Manager."""

    def read_secret(self, target: CredentialTarget) -> SecretBytes:
        """Return a secret value that must never enter the child environment."""
        ...
