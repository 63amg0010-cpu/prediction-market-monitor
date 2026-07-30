from __future__ import annotations

import pytest
from pydantic import ValidationError
from scripts.release_privacy import (
    model_sha256,
    privacy_contain,
    privacy_purge,
    privacy_verify,
)
from scripts.release_privacy_models import MatrixBProof
from tests.unit.scripts.todo11_privacy_stubs import (
    EPOCH_ID,
    SOURCE_ID,
    StubDatabase,
    StubGitHub,
    StubProvider,
    frozen_targets,
    matrix_b,
    scope,
)


@pytest.mark.asyncio
async def test_contain_and_purge_render_exact_redacted_github_argv() -> None:
    database = StubDatabase(frozen_targets())
    github = StubGitHub()
    containment = await privacy_contain(scope(), database)
    purge = await privacy_purge(scope(), containment, database, github)

    root = "/repos/63amg0010-cpu/prediction-market-monitor/actions"
    encoded_cache = (
        "scope%2Fbad%20key%3Ftoken%3Dsuper-secret%26x%3D1"
    )
    assert [command.argv for command in github.commands] == [
        ("gh", "api", "--method", "DELETE", f"{root}/artifacts/7319"),
        ("gh", "api", "--method", "POST", f"{root}/runs/8811/cancel"),
        ("gh", "api", "--method", "DELETE", f"{root}/runs/8811/logs"),
        ("gh", "api", "--method", "DELETE", f"{root}/runs/9912/logs"),
        (
            "gh",
            "api",
            "--method",
            "DELETE",
            f"{root}/caches?key={encoded_cache}",
        ),
    ]
    public_receipt = purge.model_dump_json()
    for protected in ("7319", "8811", "9912", "super-secret", "scope/bad"):
        assert protected not in public_receipt
    assert purge.deleted_row_count == 12
    assert database.real_production_calls == github.real_network_calls == 0


@pytest.mark.asyncio
async def test_only_privacy_verify_requests_terminal_restored() -> None:
    database = StubDatabase(frozen_targets())
    github = StubGitHub()
    provider = StubProvider()
    containment = await privacy_contain(scope(), database)
    purge = await privacy_purge(scope(), containment, database, github)
    assert database.restored_calls == 0

    receipt = await privacy_verify(
        scope(),
        containment,
        purge,
        matrix_b(),
        database,
        github,
        provider,
    )

    assert receipt.status == "RESTORED"
    assert receipt.durable_state == "restored"
    assert database.restored_calls == 1
    assert database.real_production_calls == 0
    assert github.real_network_calls == provider.real_network_calls == 0


@pytest.mark.asyncio
async def test_unverifiable_provider_retention_is_privacy_hold() -> None:
    database = StubDatabase(frozen_targets())
    github = StubGitHub()
    provider = StubProvider()
    provider.result = provider.result.model_copy(
        update={"provider_log_search_conclusive": False}
    )
    containment = await privacy_contain(scope(), database)
    purge = await privacy_purge(scope(), containment, database, github)

    receipt = await privacy_verify(
        scope(),
        containment,
        purge,
        matrix_b(),
        database,
        github,
        provider,
    )

    assert receipt.status == "PRIVACY_HOLD"
    assert receipt.durable_state == "restore_writing"
    assert receipt.hold_reasons == ("provider_log_search_inconclusive",)
    assert database.restored_calls == 0


def test_ordinary_rollback_shortcut_is_rejected() -> None:
    payload = matrix_b().model_dump()
    payload["command"] = "rollback-finalize"
    payload["durable_state"] = "restored"
    with pytest.raises(ValidationError):
        _ = MatrixBProof.model_validate(payload)


def test_receipt_hash_is_stable_and_contains_no_raw_scope_ids() -> None:
    value = scope()
    public_hash = model_sha256(value)
    assert len(public_hash) == 64
    assert str(SOURCE_ID) not in public_hash
    assert str(EPOCH_ID) not in public_hash
