import gzip
from dataclasses import replace

from app.reporting.formula import project_report
from app.reporting.reproduction import (
    ManifestCorrupt,
    ReproducedReport,
    RetainedReport,
    reproduce_report,
)

from .factories import manifest_payload


def test_reproduction_uses_uncompressed_identity_not_gzip_metadata() -> None:
    # Given: one retained report whose manifest is recompressed with another mtime.
    build = project_report(manifest_payload(()))
    alternate = gzip.compress(build.manifest.canonical_bytes, mtime=123456)
    envelope = replace(build.manifest.envelope, compressed_payload=alternate)
    retained = RetainedReport(
        manifest=envelope,
        report_payload=build.canonical_bytes,
        report_payload_sha256=build.payload_sha256,
    )

    # When: reproduction reads only retained payload bytes and hashes.
    result = reproduce_report(retained)

    # Then: compressor metadata cannot change report identity or bytes.
    assert isinstance(result, ReproducedReport)
    assert result.report_payload == build.canonical_bytes
    assert result.report_payload_sha256 == build.payload_sha256


def test_reproduction_fails_closed_on_stored_projection_difference() -> None:
    # Given: a valid manifest paired with a changed stored projection.
    build = project_report(manifest_payload(()))
    retained = RetainedReport(
        manifest=build.manifest.envelope,
        report_payload=build.canonical_bytes + b" ",
        report_payload_sha256=build.payload_sha256,
    )

    # When: payload-only reproduction compares canonical bytes and hash.
    result = reproduce_report(retained)

    # Then: it reports immutable corruption instead of returning stored scalars.
    assert isinstance(result, ManifestCorrupt)
    assert result.reason == "report_projection_mismatch"
