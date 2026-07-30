"""Concrete read-only ProductionProbe assembled from CLI-owned inputs."""

# pyright: reportArgumentType=false, reportImplicitOverride=false
# pyright: reportUnannotatedClassAttribute=false, reportUnnecessaryCast=false
# ruff: noqa: D105, D107, EM101, PLR0913, TC003

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy.ext.asyncio import create_async_engine

from .release_chain_common import ReleaseChainError
from .release_production_models import (
    DeploymentProof,
    ProductionObservation,
    ProductionProbeQuery,
)
from .runtime_production_adapter_database import read_database
from .runtime_production_adapter_evidence import PreparedEvidence, prepare_evidence
from .runtime_production_adapter_http import HttpGet, ProductionHttpProbe

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from .release_production_models import ProductionRequest


class ProductionRuntimeProbe:
    """Single-use adapter whose constructor completes all offline preflight."""

    def __init__(
        self,
        *,
        evidence: PreparedEvidence,
        engine: AsyncEngine,
        database_url_env: str,
        api_url: str,
        web_url: str,
        expected_revision: str,
        http: ProductionHttpProbe,
    ) -> None:
        self._evidence = evidence
        self._engine = engine
        self._database_url_env = database_url_env
        self._api_url = api_url
        self._web_url = web_url
        self._expected_revision = expected_revision
        self._http = http
        self._used = False
        self._database_now: datetime | None = None

    def observe(self, query: ProductionProbeQuery) -> ProductionObservation:
        """Read one DB snapshot, check HTTPS parity, and return hashes only."""
        if self._used:
            raise ReleaseChainError("production_probe_single_use")
        self._require_query(query)
        self._used = True
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(self._observe(query))

    async def _observe(
        self,
        query: ProductionProbeQuery,
    ) -> ProductionObservation:
        try:
            snapshot = await read_database(self._engine, self._evidence)
            self._database_now = snapshot.search.database_now
            self._http.verify(
                api_url=query.api_url,
                web_url=query.web_url,
                snapshot=snapshot,
            )
            deployments = tuple(
                DeploymentProof(
                    kind=cast("object", item.kind),
                    project_name=item.project_name,
                    project_identity_sha256=item.project_identity_sha256,
                    deployment_identity_sha256=item.deployment_identity_sha256,
                    team_identity_sha256=item.team_identity_sha256,
                    state=item.state,
                    production=item.production,
                    reviewed_sha=item.reviewed_sha,
                    protected_identity_match=True,
                    health_ok=True,
                    health_database_backed=True,
                )
                for item in self._evidence.deployments
            )
            return ProductionObservation(
                cast("tuple[DeploymentProof, ...]", deployments),
                snapshot.database,
                snapshot.search,
            )
        finally:
            await self._engine.dispose()

    def clock(self) -> datetime:
        """Return the transaction timestamp after the snapshot completed."""
        if self._database_now is None:
            raise ReleaseChainError("production_database_clock_unavailable")
        return self._database_now

    def _require_query(self, query: ProductionProbeQuery) -> None:
        expected = (
            self._database_url_env,
            self._api_url,
            self._web_url,
            self._evidence.bindings.reviewed_sha,
            self._expected_revision,
            True,
        )
        actual = (
            query.database_url_env,
            query.api_url,
            query.web_url,
            query.expected_sha,
            query.expected_revision,
            query.read_only,
        )
        if actual != expected:
            raise ReleaseChainError("production_probe_query_mismatch")

    def __repr__(self) -> str:
        return "ProductionRuntimeProbe(redacted=True)"


def production_probe_for(
    request: ProductionRequest,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[[str], AsyncEngine] = create_async_engine,
    http_get: HttpGet | None = None,
) -> ProductionRuntimeProbe:
    """CLI/runtime constructor hook; proof and env fail before any I/O."""
    evidence = prepare_evidence(request)
    source = os.environ if environ is None else environ
    database_url = source.get(request.database_url_env)
    if not request.database_url_env or not database_url:
        raise ReleaseChainError("database_url_environment_empty")
    engine = engine_factory(database_url)
    return ProductionRuntimeProbe(
        evidence=evidence,
        engine=engine,
        database_url_env=request.database_url_env,
        api_url=request.api_url,
        web_url=request.web_url,
        expected_revision=request.expected_revision,
        http=ProductionHttpProbe(get=http_get),
    )


__all__ = ("ProductionRuntimeProbe", "production_probe_for")
