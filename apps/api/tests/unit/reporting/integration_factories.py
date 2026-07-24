from datetime import UTC, datetime
from uuid import UUID

from app.domain.enums import ReportRole, Sentiment
from app.reporting.formula import project_report
from app.reporting.inputs import AnalysisSnapshot, ReportRecord, TopicMatchSnapshot
from app.reporting.manifest_schema import ReportInputManifest
from app.reporting.reconciliation import ReconcileRequest
from app.reporting.report_schema import DailyReportPayload

from .factories import (
    digest,
    manifest_payload,
    record,
    rule_match,
    selected_engagement,
    valid_analysis,
)


def relevant_analysis(
    seed: int,
    sentiment: Sentiment | None = None,
) -> AnalysisSnapshot:
    return valid_analysis(seed, relevance=True, sentiment=sentiment)


def relevant_record(
    seed: int,
    role: ReportRole,
    sentiment: Sentiment | None = None,
) -> ReportRecord:
    return record(seed, role, relevant_analysis(seed, sentiment))


def category_tie_report() -> DailyReportPayload:
    records: list[ReportRecord] = []
    for seed, category in enumerate(("alpha", "beta", "gamma"), start=10):
        match = rule_match(seed, "rise", category).model_copy(
            update={"rule_id": f"rule-rise-{category}"}
        )
        topic = TopicMatchSnapshot(
            topic_key="topic",
            normalized_value=category,
            analysis_schema_version="analysis-v1",
            mapped_category=category,
        )
        records.append(
            relevant_record(seed, ReportRole.PRIMARY).model_copy(
                update={
                    "rule_matches": (match,),
                    "topic_matches": (topic,) if category == "alpha" else (),
                }
            )
        )
    return project_report(manifest_payload(tuple(records))).payload


def reconcile_request(
    payload: ReportInputManifest,
    seed: int,
) -> ReconcileRequest:
    return ReconcileRequest(
        payload=payload,
        created_at=datetime(2026, 7, 23, tzinfo=UTC),
        report_id=UUID(int=seed),
        version_id=UUID(int=seed + 1000),
        manifest_id=UUID(int=seed + 2000),
    )


def late_correction_payloads() -> tuple[ReportInputManifest, ...]:
    primary = record(
        1,
        ReportRole.PRIMARY,
        relevant_analysis(1, Sentiment.POSITIVE),
    )
    comparison = record(2, ReportRole.COMPARISON, relevant_analysis(2))
    stages = [manifest_payload((primary, comparison))]
    primary = primary.model_copy(
        update={"analysis": relevant_analysis(1, Sentiment.NEGATIVE)}
    )
    stages.append(manifest_payload((primary, comparison)))
    comparison = comparison.model_copy(
        update={"rule_matches": (rule_match(2, "late", "market"),)}
    )
    stages.append(manifest_payload((primary, comparison)))
    primary = primary.model_copy(update={"engagement": selected_engagement(1, 3, None)})
    stages.append(manifest_payload((primary, comparison)))
    comparison = comparison.model_copy(
        update={"source_publication_manifest_hash": digest(9998)}
    )
    stages.append(manifest_payload((primary, comparison)))
    return tuple(stages)
