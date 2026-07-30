"""Focused no-network tests for the concrete Production adapter."""

from __future__ import annotations

import asyncio
import json
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

import scripts.runtime_production_adapter as adapter
from scripts.release_chain_common import JsonObject, ReleaseChainError
from scripts.release_production_models import ProductionProbeQuery, ProductionRequest
from scripts.runtime_production_adapter import ProductionRuntimeProbe
from scripts.runtime_production_adapter_evidence import (
    PreparedEvidence,
    derive_deployments,
)
from scripts.runtime_production_adapter_http import ProductionHttpProbe
from tests.unit.scripts.todo11_production_adapter_fakes import (
    NONCE,
    PLAN,
    SHA,
    Engine,
    FakeHttp,
    evidence,
    query,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from sqlalchemy.ext.asyncio import AsyncEngine

    class _LoopPolicy(Protocol):
        def set_event_loop(
            self,
            loop: asyncio.AbstractEventLoop | None,
        ) -> None: ...


def _probe(
    engine: Engine,
    http: FakeHttp,
    proof: PreparedEvidence,
) -> ProductionRuntimeProbe:
    return ProductionRuntimeProbe(
        evidence=proof,
        engine=cast("AsyncEngine", cast("object", engine)),
        database_url_env="DB_URL",
        api_url=query().api_url,
        web_url=query().web_url,
        expected_revision="20260727_0011",
        http=ProductionHttpProbe(get=http),
    )


def test_read_only_exact_sequence_http_parity_and_redaction() -> None:
    proof, http = evidence(), FakeHttp()
    engine = Engine(proof)
    probe = _probe(engine, http, proof)
    observed = probe.observe(query())
    calls = engine.connection.calls
    assert "REPEATABLE READ READ ONLY" in calls[0]
    assert "transaction_timestamp" in calls[1]
    assert sum("WITH latest AS" in item for item in calls) == 1
    joined = " ".join(calls).upper()
    assert all(word not in joined for word in ("INSERT ", "UPDATE ", "DELETE "))
    assert observed.database.transaction_read_only is True
    assert observed.search.fixture_evidence is observed.search.stub_evidence is False
    assert len(http.urls) == 8
    assert all(url.startswith("https://") for url in http.urls)
    assert "DB_URL" not in json.dumps(observed, default=str)
    assert repr(probe) == "ProductionRuntimeProbe(redacted=True)"
    assert engine.disposed


def test_observe_preserves_predecessor_loop_for_later_async_victim() -> None:
    proof, http = evidence(), FakeHttp()
    engine = Engine(proof)
    probe = _probe(engine, http, proof)

    async def loop_identity() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    policy = cast(
        "_LoopPolicy",
        vars(asyncio.events)["_event_loop_policy"],
    )
    policy_local = getattr(policy, "_local", None)
    previous_loop = cast(
        "asyncio.AbstractEventLoop | None",
        getattr(policy_local, "_loop", None),
    )
    loop_type = cast(
        "type[asyncio.AbstractEventLoop]",
        getattr(asyncio, "ProactorEventLoop", asyncio.SelectorEventLoop),
    )
    sentinel = loop_type()
    policy.set_event_loop(sentinel)
    try:
        predecessor = sentinel.run_until_complete(loop_identity())
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            observed = probe.observe(query())
            victim = sentinel.run_until_complete(loop_identity())
        assert observed.database.transaction_read_only is True
        assert predecessor is sentinel
        assert victim is sentinel
        assert asyncio.get_event_loop() is sentinel
        assert not sentinel.is_closed()
    finally:
        policy.set_event_loop(previous_loop)
        sentinel.close()


def test_wrong_query_and_chain_identity_hold_before_io() -> None:
    proof, http = evidence(), FakeHttp()
    engine = Engine(proof)
    probe = _probe(engine, http, proof)
    with pytest.raises(ReleaseChainError, match="query_mismatch"):
        _ = probe.observe(
            ProductionProbeQuery(
                "DB_URL",
                query().api_url,
                query().web_url,
                "0" * 40,
                "20260727_0011",
            )
        )
    assert engine.connection.calls == []
    wrong: JsonObject = {
        "command": "deployment-prestate",
        "team_identity_sha256": "9" * 64,
        "projects": [
            {
                "kind": "api",
                "project_name": "foreign",
                "project_identity_sha256": "4" * 64,
                "deployment_identity_sha256": "5" * 64,
                "ready_state": "READY",
                "environment": "production",
                "protected_source_sha": SHA,
            }
        ],
    }
    with pytest.raises(ReleaseChainError, match="identity_wrong"):
        _ = derive_deployments((wrong,), SHA)
    assert http.urls == []


def test_missing_env_holds_before_engine_network_or_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof, http = evidence(), FakeHttp()

    def prepared(_request: ProductionRequest) -> PreparedEvidence:
        return proof

    monkeypatch.setattr(adapter, "prepare_evidence", prepared)
    request = ProductionRequest(
        "DB_URL",
        query().api_url,
        query().web_url,
        SHA,
        PLAN,
        NONCE,
        Path("p"),
        "20260727_0011",
        Path("a"),
        Path("f"),
        Path("c"),
        Path("o"),
    )
    factory_calls: list[str] = []

    def factory(url: str) -> AsyncEngine:
        factory_calls.append(url)
        return cast("AsyncEngine", object())

    with pytest.raises(ReleaseChainError, match="environment_empty"):
        _ = adapter.production_probe_for(
            request,
            environ={},
            engine_factory=cast("Callable[[str], AsyncEngine]", factory),
            http_get=http,
        )
    assert factory_calls == []
    assert http.urls == []
