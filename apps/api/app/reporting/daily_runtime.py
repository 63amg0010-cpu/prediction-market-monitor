"""Shared bounded-daily execution values and error policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.cron import DailyOutcomeStatus

from .formula import ManifestCorruptError
from .input_policy import ReportAssemblyError
from .manifest import ManifestIntegrityError
from .retention import RetentionIntegrityError

if TYPE_CHECKING:
    from datetime import date

MAX_CATCH_UP_DAYS = 7
RECONCILIATION_FAILURES: tuple[type[Exception], ...] = (
    ReportAssemblyError,
    RetentionIntegrityError,
    ManifestIntegrityError,
    ManifestCorruptError,
    ValidationError,
    SQLAlchemyError,
    OSError,
    TimeoutError,
    RuntimeError,
    ValueError,
)


@dataclass(frozen=True, slots=True)
class DailyJobOutcome:
    """Redacted result used to sequence one kind of daily work."""

    status: DailyOutcomeStatus
    error_code: str | None

    @property
    def allows_successor(self) -> bool:
        """Return whether the next date may run for the same job kind."""
        return self.status in {
            DailyOutcomeStatus.SUCCEEDED,
            DailyOutcomeStatus.SKIPPED,
        }


def catch_up_dates(first: date, latest: date) -> tuple[date, ...]:
    """Return at most seven ascending complete Seoul dates."""
    count = min(MAX_CATCH_UP_DAYS, (latest - first).days + 1)
    return tuple(first + timedelta(days=offset) for offset in range(count))
