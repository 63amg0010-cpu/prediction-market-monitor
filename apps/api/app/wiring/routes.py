"""Single registration point for every deployed HTTP route."""

from __future__ import annotations

from secrets import token_urlsafe
from typing import TYPE_CHECKING

from pydantic import SecretStr

from app.api.routes.auth import create_auth_router
from app.api.routes.collector import create_collector_router
from app.api.routes.commands import create_commands_router
from app.api.routes.cron import create_cron_router
from app.api.routes.dashboard import create_dashboard_router
from app.api.routes.health import create_health_router
from app.api.routes.posts import create_posts_router
from app.api.routes.reports import create_reports_router
from app.api.routes.service_tokens import create_service_token_router
from app.api.routes.verification import create_verification_router
from app.api.routes.worker import create_worker_router
from app.services.dashboard.sql_health import SqlAlchemyHealthProbe
from app.services.dashboard.sql_reader import SqlAlchemyDashboardReader
from app.services.identity.cron import CronCredentialVerifier

from .unavailable_handlers import (
    UnavailableAdminCommandHandler,
    UnavailableCollectionRepository,
    UnavailableDailyCronHandler,
    UnavailableDashboardReader,
    UnavailableVerificationHandler,
    UnavailableWorkerHandler,
)
from .unavailable_identity import (
    UnavailableAdminMutationAuthorizer,
    UnavailableAuthHandler,
    UnavailableScopeAuthorizer,
    UnavailableServiceTokenHandler,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .dependency_models import AppDependencies


def include_application_routes(
    application: FastAPI,
    dependencies: AppDependencies,
    version: str,
) -> None:
    """Register every public surface with a real or fail-closed adapter."""
    auth_handler = dependencies.auth_handler or UnavailableAuthHandler()
    service_token_handler = (
        dependencies.service_token_handler or UnavailableServiceTokenHandler()
    )
    scope_authorizer = dependencies.scope_authorizer or UnavailableScopeAuthorizer()
    dashboard_reader = dependencies.dashboard_reader or (
        SqlAlchemyDashboardReader(dependencies.sessions)
        if dependencies.sessions is not None
        else UnavailableDashboardReader()
    )
    collector_handler = (
        dependencies.collector_handler or UnavailableCollectionRepository()
    )
    admin_authorizer = (
        dependencies.admin_mutation_authorizer or UnavailableAdminMutationAuthorizer()
    )
    admin_handler = (
        dependencies.admin_command_handler or UnavailableAdminCommandHandler()
    )
    verification_handler = (
        dependencies.verification_handler or UnavailableVerificationHandler()
    )
    worker_handler = dependencies.worker_handler or UnavailableWorkerHandler()
    cron_verifier = dependencies.cron_verifier or CronCredentialVerifier(
        SecretStr(token_urlsafe(48))
    )
    cron_handler = dependencies.daily_cron_handler or UnavailableDailyCronHandler()
    health_probe = dependencies.health_probe or SqlAlchemyHealthProbe(
        dependencies.sessions
    )

    application.include_router(create_health_router(health_probe, version=version))
    application.include_router(create_auth_router(auth_handler))
    application.include_router(create_service_token_router(service_token_handler))
    application.include_router(
        create_collector_router(scope_authorizer, collector_handler)
    )
    application.include_router(
        create_verification_router(scope_authorizer, verification_handler)
    )
    application.include_router(
        create_dashboard_router(scope_authorizer, dashboard_reader)
    )
    application.include_router(create_posts_router(scope_authorizer, dashboard_reader))
    application.include_router(
        create_reports_router(scope_authorizer, dashboard_reader)
    )
    application.include_router(create_commands_router(admin_authorizer, admin_handler))
    application.include_router(create_worker_router(scope_authorizer, worker_handler))
    application.include_router(create_cron_router(cron_verifier, cron_handler))


__all__ = ["include_application_routes"]
