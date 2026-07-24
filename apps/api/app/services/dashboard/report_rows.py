"""Retained-only parsing and verification of PostgreSQL report rows."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - Pydantic resolves at runtime.
from decimal import Decimal
from typing import ClassVar
from uuid import UUID  # noqa: TC003 - Pydantic resolves at runtime.

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (  # noqa: TC001 - Pydantic runtime fields.
    ManifestCodec,
    ReportStatus,
)
from app.reporting.coverage import (
    SourceCoverage,  # noqa: TC001 - Pydantic runtime field.
)
from app.reporting.inputs import Sha256Hex  # noqa: TC001 - Pydantic runtime field.
from app.reporting.manifest import ManifestEnvelope
from app.reporting.report_schema import (  # noqa: TC001 - Pydantic runtime fields.
    Highlight,
    RisingKeyword,
)
from app.reporting.reproduction import (
    ReproducedReport,
    RetainedReport,
    reproduce_report,
)

from .models import ReportItem, ReproductionStatus


class ReportRow(BaseModel):
    """Latest report revision plus every retained reproduction artifact."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    id: UUID
    report_date_seoul: date
    revision: int
    status: ReportStatus
    candidate_count: int
    relevant_count: int
    pending_count: int
    analysis_coverage: str | None
    comments_sum: int | None
    score_sum: int | None
    highlights: tuple[Highlight, ...]
    rising_keywords: tuple[RisingKeyword, ...]
    source_coverage: tuple[SourceCoverage, ...]
    manifest_id: UUID
    input_set_hash: Sha256Hex
    manifest_payload_sha256: Sha256Hex
    report_payload_sha256: Sha256Hex
    manifest_input_set_hash: Sha256Hex
    manifest_report_date_seoul: date
    manifest_report_revision: int
    manifest_codec: ManifestCodec
    compressed_manifest_payload: bytes
    manifest_uncompressed_byte_length: int
    report_payload: bytes
    created_at: datetime

    def projection(self) -> ReportItem:
        """Expose verified only after retained schema, bytes, and hashes replay."""
        coverage = (
            None if self.analysis_coverage is None else Decimal(self.analysis_coverage)
        )
        return ReportItem(
            id=self.id,
            report_date_seoul=self.report_date_seoul,
            revision=self.revision,
            status=self.status,
            candidate_count=self.candidate_count,
            relevant_count=self.relevant_count,
            pending_count=self.pending_count,
            analysis_coverage=coverage,
            comments_sum=self.comments_sum,
            score_sum=self.score_sum,
            highlights=self.highlights,
            rising_keywords=self.rising_keywords,
            source_coverage=self.source_coverage,
            manifest_id=self.manifest_id,
            input_set_hash=self.input_set_hash,
            manifest_payload_sha256=self.manifest_payload_sha256,
            report_payload_sha256=self.report_payload_sha256,
            reproduction_status=_reproduction_status(self),
            created_at=self.created_at,
        )


def _reproduction_status(row: ReportRow) -> ReproductionStatus:
    identity_matches = (
        row.input_set_hash == row.manifest_input_set_hash
        and row.report_date_seoul == row.manifest_report_date_seoul
        and row.revision == row.manifest_report_revision
    )
    retained = RetainedReport(
        manifest=ManifestEnvelope(
            codec=row.manifest_codec,
            compressed_payload=row.compressed_manifest_payload,
            uncompressed_byte_length=row.manifest_uncompressed_byte_length,
            manifest_payload_sha256=row.manifest_payload_sha256,
            input_set_hash=row.manifest_input_set_hash,
        ),
        report_payload=row.report_payload,
        report_payload_sha256=row.report_payload_sha256,
    )
    reproduced = reproduce_report(retained)
    if identity_matches and isinstance(reproduced, ReproducedReport):
        return ReproductionStatus.VERIFIED
    return ReproductionStatus.UNVERIFIED
