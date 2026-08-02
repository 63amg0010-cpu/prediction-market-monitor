"""Immutable provider quota evidence contract."""

from typing import Final

CAPTURE_FIELDS: Final = frozenset(
    {
        "schema",
        "provider",
        "public_project",
        "identity_sha256",
        "identity_bindings",
        "captured_at",
        "plan",
        "paid_enabled",
        "overage_enabled",
        "quota_status",
        "dimensions",
        "response_sha256",
        "screenshot_sha256",
        "source_url_class",
        "source_url",
        "source_url_sha256",
    }
)
PRIVATE_OBSERVATION_FIELDS: Final = frozenset(
    {
        "schema",
        "provider",
        "public_project",
        "captured_at",
        "plan",
        "paid_enabled",
        "overage_enabled",
        "quota_status",
        "dimensions",
        "source_url_class",
        "source_url",
    }
)
SUPABASE_POLICY_FIELD: Final = "non_applicable_dimensions"
SUPABASE_CAPTURE_FIELDS: Final = CAPTURE_FIELDS | frozenset({SUPABASE_POLICY_FIELD})
SUPABASE_PRIVATE_OBSERVATION_FIELDS: Final = PRIVATE_OBSERVATION_FIELDS | frozenset(
    {SUPABASE_POLICY_FIELD}
)
SUPABASE_EXCLUSION_FIELDS: Final = frozenset(
    {
        "name",
        "status",
        "reason_code",
        "policy_url",
        "policy_sha256",
        "retrieved_at",
        "account_status_sha256",
    }
)
VERIFIED_FIELDS: Final = CAPTURE_FIELDS | frozenset(
    {"phase", "reviewed_sha", "input_sha256", "receipt_sha256"}
)
OPTIONAL_CHAIN_FIELDS: Final = frozenset(
    {
        "expected_plan_sha256",
        "activation_nonce",
        "predecessor_receipt",
        "materialization_predecessor_sha256",
        "materialization_receipt_sha256",
    }
)
MATERIALIZED_CAPTURE_FIELDS: Final = frozenset(
    {
        "schema",
        "capture",
        "reviewed_sha",
        "approved_plan_sha256",
        "activation_nonce",
        "phase",
        "predecessor_receipt_sha256",
        "receipt_sha256",
    }
)
DIMENSION_FIELDS: Final = frozenset(
    {
        "name",
        "window_id",
        "observed_usage",
        "added_usage_raw",
        "quota",
        "window_kind",
        "window_start",
        "window_end",
        "status",
        "projection_operands",
    }
)
PROJECTION_OPERAND_FIELDS: Final = frozenset(
    {
        "traffic",
        "workflow_attempts",
        "deployment_attempts",
        "artifacts",
        "encrypted_backup",
    }
)
IDENTITY_BINDING_FIELDS: Final = frozenset({"role", "sha256"})
TRAFFIC_FIELDS: Final = frozenset(
    {"trailing_30d_page_requests", "units_per_page_request"}
)
ATTEMPT_FIELDS: Final = frozenset(
    {
        "kind",
        "possible_at",
        "min_attempts",
        "max_attempts",
        "rejected_duplicate_orphan_attempts",
        "units_per_attempt",
    }
)
DEPLOYMENT_FIELDS: Final = frozenset(
    {
        "project",
        "operation",
        "possible_at",
        "max_attempts",
        "successful_replacement_builds",
        "units_per_attempt",
    }
)
ARTIFACT_FIELDS: Final = frozenset(
    {
        "category",
        "raw_measured_bytes",
        "attempts",
        "retention_hours",
        "units_per_gib_hour",
    }
)
BACKUP_FIELDS: Final = frozenset(
    {
        "last_successful_encrypted_backup_bytes",
        "current_logical_size_estimate_bytes",
        "attempts",
        "retention_hours",
        "units_per_gib_hour",
    }
)
PROVIDER_PROJECTS: Final = {
    "github": "63amg0010-cpu/prediction-market-monitor",
    "vercel-api": "prediction-monitor-api",
    "vercel-web": "prediction-monitor-web",
    "supabase": "redacted-supabase-project",
}
PROVIDER_PLANS: Final = {
    "github": "public-standard",
    "vercel-api": "hobby",
    "vercel-web": "hobby",
    "supabase": "free",
}
PROVIDER_HOSTS: Final = {
    "github": frozenset({"api.github.com", "docs.github.com", "github.com"}),
    "vercel-api": frozenset({"api.vercel.com", "vercel.com"}),
    "vercel-web": frozenset({"api.vercel.com", "vercel.com"}),
    "supabase": frozenset({"api.supabase.com", "supabase.com"}),
}
PROVIDER_PUBLIC_SOURCE_URLS: Final = {
    "github": "https://docs.github.com/en/billing/reference/product-usage-included",
    "vercel-api": "https://vercel.com/docs/limits",
    "vercel-web": "https://vercel.com/docs/limits",
    "supabase": "https://supabase.com/docs/guides/platform/billing-on-supabase",
}
PROVIDER_DIMENSIONS: Final = {
    "github": frozenset(
        {
            "github_actions_minutes",
            "github_artifact_gb_hours",
            "github_packages_gb_hours",
            "github_cache_bytes",
        }
    ),
    "vercel-api": frozenset({"vercel_api_invocations"}),
    "vercel-web": frozenset(
        {
            "vercel_web_invocations",
            "vercel_cpu_ms",
            "vercel_memory_gb_seconds",
            "vercel_transfer_bytes",
            "vercel_deployments",
        }
    ),
    "supabase": frozenset(
        {
            "supabase_database_bytes",
            "supabase_uncached_egress_bytes",
            "supabase_cached_egress_bytes",
            "supabase_storage_bytes",
            "supabase_mau",
            "supabase_edge_invocations",
            "supabase_realtime_messages",
        }
    ),
}
SUPABASE_REQUIRED_EXCLUSIONS: Final = frozenset(
    {
        "supabase_disk_iops_addon",
        "supabase_disk_throughput_addon",
        "supabase_logs_ingest",
    }
)
SUPABASE_EXCLUSION_REASONS: Final = {
    "supabase_disk_iops_addon": "provisioned_disk_addon_not_enabled",
    "supabase_disk_throughput_addon": "provisioned_disk_addon_not_enabled",
    "supabase_logs_ingest": "billing_enforcement_not_live",
}
SUPABASE_EXCLUSION_URLS: Final = {
    "supabase_disk_iops_addon": (
        "https://supabase.com/docs/guides/platform/manage-your-usage/disk-iops"
    ),
    "supabase_disk_throughput_addon": (
        "https://supabase.com/docs/guides/platform/manage-your-usage/disk-iops"
    ),
    "supabase_logs_ingest": (
        "https://supabase.com/docs/guides/platform/manage-your-usage/logs"
    ),
}
PROVIDER_IDENTITY_ENVS: Final = {
    "github": ("GITHUB_REPOSITORY_ID",),
    "vercel-api": ("VERCEL_ORG_ID", "VERCEL_API_PROJECT_ID"),
    "vercel-web": ("VERCEL_ORG_ID", "VERCEL_WEB_PROJECT_ID"),
    "supabase": ("SUPABASE_ORG_ID", "SUPABASE_PROJECT_ID"),
}
PROVIDER_IDENTITY_ROLES: Final = {
    "github": ("repository",),
    "vercel-api": ("org", "project"),
    "vercel-web": ("org", "project"),
    "supabase": ("org", "project"),
}
REQUIRED_WORKFLOW_KINDS: Final = frozenset(
    {"collect", "verify", "activation", "migration"}
)
REQUIRED_DEPLOYMENT_OPERATIONS: Final = frozenset(
    {
        "initial-api",
        "initial-web",
        "alias-api",
        "alias-web",
        "split-api",
        "split-web",
        "matrix-b-api",
        "matrix-b-web",
    }
)
REQUIRED_ARTIFACT_CATEGORIES: Final = frozenset(
    {
        "activation_evidence",
        "migration_backup_ciphertext",
        "ci_test_build_outputs",
        "local_nonproduction_playwright",
        "rollback_receipts",
        "cadence_receipts",
    }
)
DECEMBER: Final = 12
