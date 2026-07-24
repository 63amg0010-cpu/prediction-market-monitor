"""Payload-only daily report reproduction."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from pydantic import ValidationError

from .formula import ManifestCorruptError, project_report
from .manifest import ManifestEnvelope, ManifestIntegrityError, read_manifest
from .report_schema import DailyReportPayload


@dataclass(frozen=True, slots=True)
class RetainedReport:
    """Only retained bytes and hashes available to the reproduction path."""

    manifest: ManifestEnvelope
    report_payload: bytes
    report_payload_sha256: str


@dataclass(frozen=True, slots=True)
class ReproducedReport:
    """Byte-equal report projection recomputed from its manifest payload."""

    status: Literal["reproduced"]
    payload: DailyReportPayload
    report_payload: bytes
    report_payload_sha256: str


@dataclass(frozen=True, slots=True)
class ManifestCorrupt:
    """Immutable fail-closed outcome without a source-row fallback."""

    status: Literal["manifest_corrupt"]
    reason: str


type ReproductionResult = ReproducedReport | ManifestCorrupt


def reproduce_report(retained: RetainedReport) -> ReproductionResult:
    """Recompute a report using only the retained manifest and projection bytes."""
    try:
        payload = read_manifest(retained.manifest)
        recomputed = project_report(payload)
        stored_payload = DailyReportPayload.model_validate_json(retained.report_payload)
    except (ManifestIntegrityError, ManifestCorruptError, ValidationError) as error:
        return ManifestCorrupt(status="manifest_corrupt", reason=str(error))
    stored_hash = sha256(retained.report_payload).hexdigest()
    projection_matches = (
        retained.report_payload == recomputed.canonical_bytes
        and retained.report_payload_sha256 == recomputed.payload_sha256
        and stored_hash == retained.report_payload_sha256
        and stored_payload == recomputed.payload
    )
    if not projection_matches:
        return ManifestCorrupt(
            status="manifest_corrupt",
            reason="report_projection_mismatch",
        )
    return ReproducedReport(
        status="reproduced",
        payload=recomputed.payload,
        report_payload=recomputed.canonical_bytes,
        report_payload_sha256=recomputed.payload_sha256,
    )
