"""Typed application dependency bundle for the composition root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.api.routes.auth import AdminAuthHandler
    from app.api.routes.commands import AdminCommandHandler, AdminMutationAuthorizer
    from app.api.routes.cron import DailyCronHandler
    from app.api.routes.health import DatabaseHealthProbe
    from app.api.routes.verification import VerificationHandler
    from app.api.routes.worker import WorkerHandler
    from app.collection.repository import CollectionRepository
    from app.core.settings import IdentitySettings
    from app.db.session import DatabaseSessions
    from app.services.dashboard.ports import DashboardReader, ScopeAuthorizer
    from app.services.identity.cron import CronCredentialVerifier
    from app.services.identity.exchanges import ServiceTokenExchangeHandler


@dataclass(frozen=True, slots=True)
class AppDependencies:
    """Explicit adapters accepted by the application composition root."""

    settings: IdentitySettings | None = None
    sessions: DatabaseSessions | None = None
    auth_handler: AdminAuthHandler | None = None
    service_token_handler: ServiceTokenExchangeHandler | None = None
    scope_authorizer: ScopeAuthorizer | None = None
    dashboard_reader: DashboardReader | None = None
    collector_handler: CollectionRepository | None = None
    admin_mutation_authorizer: AdminMutationAuthorizer | None = None
    admin_command_handler: AdminCommandHandler | None = None
    verification_handler: VerificationHandler | None = None
    worker_handler: WorkerHandler | None = None
    cron_verifier: CronCredentialVerifier | None = None
    daily_cron_handler: DailyCronHandler | None = None
    health_probe: DatabaseHealthProbe | None = None


__all__ = ("AppDependencies",)
