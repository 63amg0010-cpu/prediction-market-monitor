from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.services.configuration import (
    BudgetState,
    ConfigurationInvariantError,
    ConfigurationParseError,
    evaluate_free_budget,
    load_all_configurations,
    load_metrics_config,
    load_sources_config,
    parse_categories_config,
    parse_sources_config,
)
from pydantic import BaseModel, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[6]
CONFIG = ROOT / "config"

type RawValue = (
    str | int | float | bool | None | list["RawValue"] | dict[str, "RawValue"]
)
type RawDocument = dict[str, RawValue]
RAW_ADAPTER: TypeAdapter[RawDocument] = TypeAdapter(RawDocument)


def raw_document(model: BaseModel) -> RawDocument:
    """Convert a model to a typed mutable test document."""
    result: RawDocument = RAW_ADAPTER.validate_json(model.model_dump_json())
    return result


def source_rows(raw: RawDocument) -> list[dict[str, RawValue]]:
    """Narrow the source list at the test boundary."""
    value = raw["sources"]
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return [item for item in value if isinstance(item, dict)]


def test_reviewed_files_load_fail_closed_and_hashable() -> None:
    configs = load_all_configurations(CONFIG)

    assert configs.sources.scope_version == "phase1-reviewed-v1"
    assert all(not source.enabled for source in configs.sources.sources)
    assert all(
        source.authorization.status == "pending" for source in configs.sources.sources
    )
    assert configs.sources.canonical_sha256 != configs.keywords.canonical_sha256
    assert len(configs.metrics.canonical_sha256) == 64
    assert configs.categories.default_category == "uncategorized"


def test_reordering_yaml_mapping_does_not_change_hash() -> None:
    first = load_sources_config(CONFIG / "sources.reviewed.yml")
    raw = raw_document(first)
    reordered = {key: raw[key] for key in reversed(tuple(raw))}

    assert parse_sources_config(reordered).canonical_sha256 == first.canonical_sha256


def test_enabled_source_without_approval_evidence_is_rejected() -> None:
    raw = raw_document(load_sources_config(CONFIG / "sources.reviewed.yml"))
    rows = source_rows(raw)
    rows[0]["enabled"] = True
    rows[0]["state"] = "enabled"

    with pytest.raises((ValidationError, ConfigurationInvariantError)):
        _ = parse_sources_config(raw)


def test_expired_and_revoked_authorization_are_rejected_when_enabled() -> None:
    raw = raw_document(load_sources_config(CONFIG / "sources.reviewed.yml"))
    source = source_rows(raw)[0]
    source["enabled"] = True
    source["state"] = "enabled"
    source["authorization"] = {
        "status": "approved",
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "location": "https://example.invalid/evidence",
                "sha256": "a" * 64,
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
        ],
        "effective_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "permitted_methods": ["GET"],
        "permitted_routes": ["https://example.invalid/route"],
        "purpose": "reviewed collection",
    }

    with pytest.raises((ValidationError, ConfigurationInvariantError)):
        _ = parse_sources_config(raw, as_of=datetime.now(UTC))


def test_toss_and_naver_cannot_be_enabled_together() -> None:
    raw = raw_document(load_sources_config(CONFIG / "sources.reviewed.yml"))
    for source in source_rows(raw):
        if source["source_id"] in {"toss", "naver_finance"}:
            source["enabled"] = True
            source["state"] = "enabled"

    with pytest.raises((ValidationError, ConfigurationInvariantError)):
        _ = parse_sources_config(raw)


@pytest.mark.parametrize(
    ("used", "expected"),
    [
        (69, BudgetState.NORMAL),
        (70, BudgetState.SOFT_LIMITED),
        (79, BudgetState.SOFT_LIMITED),
        (80, BudgetState.HARD_BLOCKED),
    ],
)
def test_free_budget_threshold_boundaries(used: int, expected: BudgetState) -> None:
    decision = evaluate_free_budget(used=used, quota=100)

    assert decision.state is expected


def test_negative_budget_and_zero_quota_are_rejected() -> None:
    with pytest.raises(ConfigurationInvariantError):
        _ = evaluate_free_budget(used=-1, quota=100)
    with pytest.raises(ConfigurationInvariantError):
        _ = evaluate_free_budget(used=1, quota=0)


def test_metrics_encode_seoul_day_and_null_engagement() -> None:
    metrics = load_metrics_config(CONFIG / "metrics.v1.yml")
    timestamp = datetime(2026, 7, 20, 15, 30, tzinfo=UTC)

    assert metrics.storage_timezone == "UTC"
    assert metrics.report_timezone == "Asia/Seoul"
    assert metrics.report_day(timestamp).isoformat() == "2026-07-21"
    assert metrics.comparison.current_days == 7
    assert metrics.comparison.previous_days == 7
    assert metrics.engagement.unknown_value == "null"
    assert metrics.delta.zero_denominator == "null"


def test_malformed_yaml_and_duplicate_category_are_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.yml"
    _ = malformed.write_text("schema: [unterminated", encoding="utf-8")
    with pytest.raises(ConfigurationParseError):
        _ = load_metrics_config(malformed)

    raw = raw_document(load_all_configurations(CONFIG).categories)
    categories = raw["categories"]
    assert isinstance(categories, list)
    raw["categories"] = [*categories, categories[0]]
    with pytest.raises((ValidationError, ConfigurationInvariantError)):
        _ = parse_categories_config(raw)


def test_decimal_utilization_is_exact() -> None:
    decision = evaluate_free_budget(used=Decimal("70.0"), quota=Decimal("100.0"))

    assert decision.utilization == Decimal("0.70")
