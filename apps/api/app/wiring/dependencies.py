"""Fail-closed production dependency composition from process configuration."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from pydantic import SecretBytes, ValidationError

from app.collection.analysis_input_store import AnalysisQueueVersions
from app.collection.completion_store import CompletionServiceConfig
from app.collection.page_service_models import PageCommitServiceConfig
from app.collection.repository import (
    CollectionRepositoryConfig,
    SqlAlchemyCollectionRepository,
)
from app.collection.verification_repository import SqlAlchemyVerificationRepository
from app.core.principals import CredentialVersion
from app.core.settings import IdentitySettings
from app.db.session import DatabaseConfigurationError, DatabaseSessions
from app.reporting.coordinator import SqlAlchemyReportCoordinator
from app.reporting.daily import SqlAlchemyDailyCronHandler
from app.reporting.daily_jobs import SqlAlchemyDailyJobStore
from app.reporting.input_assembler import SqlAlchemyReportInputAssembler
from app.reporting.input_policy import report_assembly_policy
from app.reporting.manifest_schema import AnalysisVersionTuple
from app.reporting.retention_coordinator import SqlAlchemyRetentionCoordinator
from app.reporting.retention_sql import SqlAlchemyRetentionRepository
from app.services.admin_commands import SqlAdminCommandHandler
from app.services.configuration.errors import ConfigurationParseError
from app.services.configuration.loaders import load_all_configurations
from app.services.dashboard.sql_health import SqlAlchemyHealthProbe
from app.services.dashboard.sql_reader import SqlAlchemyDashboardReader
from app.services.identity.admin import AdminPasswordVerifier
from app.services.identity.admin_handlers import SqlAdminHandlers
from app.services.identity.cron import CronCredentialVerifier
from app.services.identity.sql_admin_rate_limit import SqlLoginFailureRepository
from app.services.identity.sql_admin_sessions import SqlAdminSessionStore

from .dependency_models import AppDependencies
from .identity_dependencies import IdentityAdapters, build_identity_adapters

if TYPE_CHECKING:
    from collections.abc import Mapping

REVIEWED_PAGE_CAP: Final = 4
REVIEWED_POST_CAP: Final = 20
ANALYSIS_VERSIONS: Final = AnalysisQueueVersions(
    prompt="relevance-v1",
    model="codex-cli-0.144.1",
    schema="analysis-output-v1",
)
_JITTER_DOMAIN: Final = b"collection-retry-jitter-v1\x00"
_MINIMUM_JITTER_SECRET_BYTES: Final = 32
_DEFAULT_CONFIG_DIR: Final = Path(__file__).parents[4] / "config"
_IDENTITY_ENV_FIELDS: Final = (
    ("api_base_url", "API_BASE_URL"),
    ("service_token_key_id", "SERVICE_TOKEN_KEY_ID"),
    ("service_token_issuer_private_key", "SERVICE_TOKEN_ISSUER_PRIVATE_KEY"),
    ("service_token_issuer_public_key", "SERVICE_TOKEN_ISSUER_PUBLIC_KEY"),
    ("bff_client_credential", "BFF_CLIENT_CREDENTIAL"),
    ("bff_credential_version", "BFF_CREDENTIAL_VERSION"),
    ("worker_bootstrap_secret", "WORKER_BOOTSTRAP_SECRET"),
    ("worker_credential_version", "WORKER_CREDENTIAL_VERSION"),
    ("cron_secret", "CRON_SECRET"),
    ("admin_password_argon2id_hash", "ADMIN_PASSWORD_ARGON2ID_HASH"),
    ("session_hmac_secret", "SESSION_HMAC_SECRET"),
    ("github_repository", "GITHUB_REPOSITORY"),
    ("github_workflow_refs", "GITHUB_WORKFLOW_REFS"),
    ("github_allowed_refs", "GITHUB_ALLOWED_REFS"),
    ("github_allowed_environments", "GITHUB_ALLOWED_ENVIRONMENTS"),
)


def dependencies_from_environment(
    environment: Mapping[str, str],
) -> AppDependencies:
    """Build only adapters whose complete production prerequisites are present."""
    settings = _identity_settings(environment)
    sessions = _database_sessions(environment)
    reader = None if sessions is None else SqlAlchemyDashboardReader(sessions)
    collector = _collection_repository(environment, sessions)
    identity = _identity_adapters(settings, sessions)
    admin = _admin_identity(environment, settings, sessions, identity)
    commands = _admin_commands(environment, sessions)
    daily = _daily_handler(environment, sessions)
    return AppDependencies(
        settings=settings,
        sessions=sessions,
        auth_handler=admin,
        dashboard_reader=reader,
        collector_handler=collector,
        service_token_handler=None if identity is None else identity.exchange,
        scope_authorizer=None if identity is None else identity.scopes,
        admin_mutation_authorizer=admin,
        admin_command_handler=commands,
        verification_handler=_verification_repository(environment, sessions),
        cron_verifier=(
            None if settings is None else CronCredentialVerifier(settings.cron_secret)
        ),
        daily_cron_handler=daily,
        health_probe=SqlAlchemyHealthProbe(sessions),
    )


def _admin_identity(
    environment: Mapping[str, str],
    settings: IdentitySettings | None,
    sessions: DatabaseSessions | None,
    identity: IdentityAdapters | None,
) -> SqlAdminHandlers | None:
    origin = _public_origin(environment.get("WEB_PUBLIC_ORIGIN"))
    if settings is None or sessions is None or identity is None or origin is None:
        return None
    secret = SecretBytes(settings.session_hmac_secret.get_secret_value().encode())
    store = SqlAdminSessionStore(
        sessions,
        signing_secret=secret,
        credential_version=CredentialVersion(settings.bff_credential_version),
        allowed_origins=frozenset({origin}),
    )
    return SqlAdminHandlers(
        scopes=identity.scopes,
        password=AdminPasswordVerifier(settings.admin_password_argon2id_hash),
        failures=SqlLoginFailureRepository(sessions, secret),
        sessions=store,
    )


def _admin_commands(
    environment: Mapping[str, str],
    sessions: DatabaseSessions | None,
) -> SqlAdminCommandHandler | None:
    scope_version = environment.get("MONITOR_SCOPE_VERSION")
    if sessions is None or scope_version is None or not scope_version.strip():
        return None
    return SqlAdminCommandHandler(sessions, scope_version)


def _daily_handler(
    environment: Mapping[str, str],
    sessions: DatabaseSessions | None,
) -> SqlAlchemyDailyCronHandler | None:
    if sessions is None:
        return None
    config_dir = Path(environment.get("MONITOR_CONFIG_DIR", _DEFAULT_CONFIG_DIR))
    try:
        configuration = load_all_configurations(config_dir)
        configured_scope = environment.get("MONITOR_SCOPE_VERSION")
        if configured_scope != configuration.sources.scope_version:
            return None
        policy = report_assembly_policy(
            configuration,
            (
                AnalysisVersionTuple(
                    prompt_version=ANALYSIS_VERSIONS.prompt,
                    model_version=ANALYSIS_VERSIONS.model,
                    schema_version=ANALYSIS_VERSIONS.schema,
                ),
            ),
        )
    except (ConfigurationParseError, OSError, ValidationError, ValueError):
        return None
    assembler = SqlAlchemyReportInputAssembler(sessions, policy)
    retention_repository = SqlAlchemyRetentionRepository(sessions)
    return SqlAlchemyDailyCronHandler(
        SqlAlchemyDailyJobStore(sessions),
        SqlAlchemyReportCoordinator(sessions, assembler),
        SqlAlchemyRetentionCoordinator(retention_repository),
    )


def _public_origin(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _identity_adapters(
    settings: IdentitySettings | None,
    sessions: DatabaseSessions | None,
) -> IdentityAdapters | None:
    if settings is None or sessions is None:
        return None
    try:
        return build_identity_adapters(settings, sessions)
    except (TypeError, ValueError):
        return None


def _verification_repository(
    environment: Mapping[str, str], sessions: DatabaseSessions | None
) -> SqlAlchemyVerificationRepository | None:
    scope_version = environment.get("MONITOR_SCOPE_VERSION")
    if sessions is None or scope_version is None or not scope_version.strip():
        return None
    return SqlAlchemyVerificationRepository(sessions, scope_version)


def _database_sessions(
    environment: Mapping[str, str],
) -> DatabaseSessions | None:
    try:
        return DatabaseSessions.from_environment(environment)
    except DatabaseConfigurationError:
        return None


def _identity_settings(
    environment: Mapping[str, str],
) -> IdentitySettings | None:
    if any(name not in environment for _, name in _IDENTITY_ENV_FIELDS):
        return None
    values = {field: environment[name] for field, name in _IDENTITY_ENV_FIELDS}
    try:
        return IdentitySettings.model_validate(values)
    except ValidationError:
        return None


def _collection_repository(
    environment: Mapping[str, str],
    sessions: DatabaseSessions | None,
) -> SqlAlchemyCollectionRepository | None:
    jitter_secret = environment.get("SESSION_HMAC_SECRET")
    if (
        sessions is None
        or jitter_secret is None
        or len(jitter_secret.encode()) < _MINIMUM_JITTER_SECRET_BYTES
    ):
        return None
    return SqlAlchemyCollectionRepository(
        sessions,
        CollectionRepositoryConfig(
            page=PageCommitServiceConfig(
                reviewed_page_cap=REVIEWED_PAGE_CAP,
                reviewed_post_cap=REVIEWED_POST_CAP,
                analysis_versions=ANALYSIS_VERSIONS,
            ),
            completion=CompletionServiceConfig(
                retry_jitter_key=sha256(
                    _JITTER_DOMAIN + jitter_secret.encode()
                ).digest()
            ),
        ),
    )
