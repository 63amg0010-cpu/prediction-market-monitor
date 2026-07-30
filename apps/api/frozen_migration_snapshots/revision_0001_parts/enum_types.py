"""Shared native PostgreSQL enum types."""

from enum import StrEnum
from typing import Final

from sqlalchemy import Enum as SAEnum

from .enums import (
    AnalysisState,
    AuthorizationStatus,
    BudgetDecisionStatus,
    CapabilityKind,
    CommandKind,
    CommandStatus,
    Country,
    JobKind,
    JobStatus,
    ManifestCodec,
    ManifestItemKind,
    NoncePurpose,
    PageItemDisposition,
    PostVersionReason,
    PrincipalKind,
    ProofStatus,
    QueueStatus,
    ReportRole,
    ReportStatus,
    RunStatus,
    Sentiment,
    SourcePlatform,
    TerminalReason,
    TombstoneDeletionReason,
    TombstoneEntityKind,
    VerificationStatus,
)


def native_enum[EnumT: StrEnum](enum_type: type[EnumT], name: str) -> SAEnum:
    """Create one value-backed native PostgreSQL enum."""

    def enum_values(members: type[EnumT]) -> list[str]:
        return [member.value for member in members]

    return SAEnum(
        enum_type,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=enum_values,
    )


AUTHORIZATION_STATUS: Final = native_enum(AuthorizationStatus, "authorization_status")
SOURCE_PLATFORM: Final = native_enum(SourcePlatform, "source_platform")
COUNTRY: Final = native_enum(Country, "country_code")
PRINCIPAL_KIND: Final = native_enum(PrincipalKind, "principal_kind")
NONCE_PURPOSE: Final = native_enum(NoncePurpose, "nonce_purpose")
COMMAND_KIND: Final = native_enum(CommandKind, "collection_command_kind")
COMMAND_STATUS: Final = native_enum(CommandStatus, "collection_command_status")
RUN_STATUS: Final = native_enum(RunStatus, "collection_run_status")
TERMINAL_REASON: Final = native_enum(TerminalReason, "terminal_reason")
PAGE_ITEM_DISPOSITION: Final = native_enum(PageItemDisposition, "page_item_disposition")
POST_VERSION_REASON: Final = native_enum(PostVersionReason, "post_version_reason")
QUEUE_STATUS: Final = native_enum(QueueStatus, "analysis_queue_status")
ANALYSIS_STATE: Final = native_enum(AnalysisState, "analysis_state")
SENTIMENT: Final = native_enum(Sentiment, "sentiment")
REPORT_ROLE: Final = native_enum(ReportRole, "report_role")
MANIFEST_ITEM_KIND: Final = native_enum(ManifestItemKind, "manifest_item_kind")
MANIFEST_CODEC: Final = native_enum(ManifestCodec, "manifest_codec")
REPORT_STATUS: Final = native_enum(ReportStatus, "report_status")
TOMBSTONE_ENTITY_KIND: Final = native_enum(TombstoneEntityKind, "tombstone_entity_kind")
TOMBSTONE_DELETION_REASON: Final = native_enum(
    TombstoneDeletionReason, "tombstone_deletion_reason"
)
VERIFICATION_STATUS: Final = native_enum(VerificationStatus, "verification_status")
JOB_KIND: Final = native_enum(JobKind, "scheduled_job_kind")
JOB_STATUS: Final = native_enum(JobStatus, "scheduled_job_status")
BUDGET_DECISION_STATUS: Final = native_enum(
    BudgetDecisionStatus, "budget_decision_status"
)
CAPABILITY_KIND: Final = native_enum(CapabilityKind, "capability_kind")
PROOF_STATUS: Final = native_enum(ProofStatus, "proof_status")
