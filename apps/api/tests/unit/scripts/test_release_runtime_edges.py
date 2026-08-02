from __future__ import annotations

# ruff: noqa: ANN401, S105, S106
# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false
# pyright: reportUnannotatedClassAttribute=false, reportUnusedCallResult=false
import asyncio
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from app.services.release.receipts import canonicalize
from scripts import release_runtime_prestate
from scripts.release_runtime_http import (
    HttpRuntimeError,
    ReadOnlyHttpProbe,
)
from scripts.release_runtime_io import (
    BoundedPathReceiptIO,
    RuntimeIOError,
    load_canonical_object,
)
from scripts.release_runtime_subprocess import (
    DispatchRuntimeRunner,
    RuntimeAdapterError,
    SecretRuntimeRunner,
    VercelRuntimeRunner,
)
from scripts.release_vercel_models import ChildCommand, VercelPrestateRequest

if TYPE_CHECKING:
    from pathlib import Path


class Calls:
    def __init__(self) -> None:
        self.values: list[dict[str, Any]] = []

    def run(self, argv: tuple[str, ...], **kwargs: Any) -> Any:
        self.values.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=b'{"ok":true}',
            stderr=b"token-value leaked",
        )


def test_dispatch_preflights_env_and_redacts_without_shell(
    tmp_path: Path,
) -> None:
    calls = Calls()
    with pytest.raises(
        RuntimeAdapterError, match="github_token_environment_empty"
    ):
        _ = DispatchRuntimeRunner(
            tmp_path,
            token_env="GH_RELEASE_TOKEN",
            environ={},
            run_process=calls.run,
        )
    assert calls.values == []

    runner = DispatchRuntimeRunner(
        tmp_path,
        token_env="GH_RELEASE_TOKEN",
        environ={"GH_RELEASE_TOKEN": "token-value"},
        run_process=calls.run,
    )
    result = runner.run(("gh", "api", "/rate_limit"))
    assert result.stderr == "[REDACTED] leaked"
    assert calls.values[0]["shell"] is False
    assert calls.values[0]["timeout"] == 30.0
    assert calls.values[0]["env"]["GH_TOKEN"] == "token-value"
    assert "token-value" not in calls.values[0]["argv"]
    assert SecretRuntimeRunner(runner).run(("gh", "api", "-"), b"secret") == 0


def test_timeout_and_secret_argv_have_redacted_codes(tmp_path: Path) -> None:
    def timeout(*_args: object, **_kwargs: object) -> Any:
        raise subprocess.TimeoutExpired(("gh",), 1)

    runner = DispatchRuntimeRunner(
        tmp_path,
        token_env="TOKEN_NAME",
        environ={"TOKEN_NAME": "super-secret"},
        run_process=timeout,
    )
    with pytest.raises(RuntimeAdapterError, match="secret_in_argv"):
        _ = runner.run(("gh", "api", "super-secret"))
    with pytest.raises(RuntimeAdapterError, match="child_timeout"):
        _ = runner.run(("gh", "api", "/safe"))


def test_vercel_resolves_all_named_env_before_child(tmp_path: Path) -> None:
    calls = Calls()
    runner = VercelRuntimeRunner(
        environ={"ORG_SOURCE": "org-secret"},
        run_process=calls.run,
    )
    command = ChildCommand(
        "inspect",
        ("npx", "vercel", "inspect"),
        tmp_path,
        {
            "VERCEL_ORG_ID_FROM_ENV": "ORG_SOURCE",
            "VERCEL_TOKEN_FROM_ENV": "TOKEN_SOURCE",
        },
    )
    with pytest.raises(
        RuntimeAdapterError, match="vercel_credential_environment_empty"
    ):
        _ = runner.execute(command)
    assert calls.values == []

    runner = VercelRuntimeRunner(
        environ={
            "ORG_SOURCE": "org-secret",
            "TOKEN_SOURCE": "token-secret",
        },
        run_process=calls.run,
    )
    _ = runner.execute(command)
    assert calls.values[0]["env"]["VERCEL_ORG_ID"] == "org-secret"
    assert calls.values[0]["env"]["VERCEL_TOKEN"] == "token-secret"
    assert calls.values[0]["shell"] is False


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("nt", ("npx.cmd", "--yes", "vercel@51.7.0", "inspect")),
        ("posix", ("npx", "--yes", "vercel@51.7.0", "inspect")),
    ],
)
def test_vercel_resolves_only_the_windows_npx_shim(
    tmp_path: Path,
    platform: str,
    expected: tuple[str, ...],
) -> None:
    calls = Calls()
    runner = VercelRuntimeRunner(
        environ={"TOKEN_SOURCE": "token-secret"},
        run_process=calls.run,
        platform=platform,
    )
    _ = runner.execute(
        ChildCommand(
            "inspect",
            ("npx", "--yes", "vercel@51.7.0", "inspect"),
            tmp_path,
            {"VERCEL_TOKEN_FROM_ENV": "TOKEN_SOURCE"},
        )
    )
    assert calls.values[0]["argv"] == expected


def test_runtime_rejects_an_unknown_execution_platform(tmp_path: Path) -> None:
    runner = VercelRuntimeRunner(
        environ={"TOKEN_SOURCE": "token-secret"},
        platform="unknown",
    )
    command = ChildCommand(
        "inspect",
        ("npx", "vercel", "inspect"),
        tmp_path,
        {"VERCEL_TOKEN_FROM_ENV": "TOKEN_SOURCE"},
    )
    with pytest.raises(
        RuntimeAdapterError, match="unsupported_execution_platform"
    ):
        _ = runner.execute(command)


def test_bounded_path_io_and_canonical_loader(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    value = {"a": 1, "b": True}
    raw = canonicalize(value)
    io = BoundedPathReceiptIO(max_bytes=100)
    io.write(path, raw)
    assert load_canonical_object(path, io=io) == value
    path.write_bytes(b'{"b":true,"a":1}')
    with pytest.raises(RuntimeIOError, match="document_not_canonical"):
        _ = load_canonical_object(path, io=io)
    path.write_bytes(b'{"a":1,"a":2}')
    with pytest.raises(RuntimeIOError, match="document_duplicate_key"):
        _ = load_canonical_object(path, io=io)


class FakeResponse:
    def __init__(
        self,
        status: int,
        content: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self.content = content
        self.headers = headers or {}


def test_http_probe_is_get_only_bounded_and_never_redirects() -> None:
    urls: list[str] = []

    def get(url: str) -> FakeResponse:
        urls.append(url)
        return FakeResponse(302, b"x", {"location": "https://other.test"})

    probe = ReadOnlyHttpProbe(get=get)
    with pytest.raises(HttpRuntimeError, match="http_redirect_forbidden"):
        _ = probe.fetch("https://api.example.test/health")
    assert urls == ["https://api.example.test/health"]
    with pytest.raises(HttpRuntimeError, match="http_url_invalid"):
        _ = probe.fetch("http://api.example.test/health")


def test_prestate_database_connection_is_disposed_on_its_own_loop() -> None:
    loop_ids: list[int] = []

    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            _error_type: object,
            _error: object,
            _traceback: object,
        ) -> None:
            return None

    class Connection:
        def begin(self) -> Transaction:
            return Transaction()

        async def execute(self, _statement: object) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))

        async def scalar(self, _statement: object) -> datetime:
            return datetime(2026, 8, 2, tzinfo=UTC)

    class ConnectionContext:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(
            self,
            _error_type: object,
            _error: object,
            _traceback: object,
        ) -> None:
            return None

    class Engine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

        async def dispose(self) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))

    observed = release_runtime_prestate._database_time_from_isolated_thread(  # pyright: ignore[reportPrivateUsage]
        Engine()
    )
    assert observed == datetime(2026, 8, 2, tzinfo=UTC)
    assert len(loop_ids) == 2
    assert len(set(loop_ids)) == 1


def test_composite_prestate_uses_the_validated_origin_main_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("approved plan", encoding="utf-8")
    requests: list[VercelPrestateRequest] = []

    def front_matter(_path: Path) -> dict[str, object]:
        return {"plan_path": "plan.md"}

    def review_bindings(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            reviewed_sha="a" * 40,
            approved_plan_sha256="b" * 64,
            approval_round_id="c" * 64,
            approval_launch_sha256s=("d" * 64, "e" * 64),
        )

    class FakeReviewAdapter:
        def __init__(self, _root: Path) -> None:
            pass

        def inspect(self, _path: Path) -> object:
            return object()

    def prestate(
        request: VercelPrestateRequest,
        _runner: object,
    ) -> dict[str, object]:
        requests.append(request)
        return {
            "deployment_id": f"deployment-{request.project_kind}",
            "deployment_url": f"https://{request.project_kind}.vercel.app",
            "alias": request.alias,
            "protected_source_sha": "a" * 40,
        }

    monkeypatch.setattr(
        release_runtime_prestate,
        "_review_front_matter",
        front_matter,
    )
    monkeypatch.setattr(
        release_runtime_prestate,
        "validate_review_record",
        review_bindings,
    )
    monkeypatch.setattr(
        release_runtime_prestate,
        "GitStatReviewAdapter",
        FakeReviewAdapter,
    )
    monkeypatch.setattr(
        release_runtime_prestate,
        "run_vercel_prestate",
        prestate,
    )

    async def database_time(_engine: object) -> datetime:
        return datetime(2026, 8, 2, tzinfo=UTC)

    monkeypatch.setattr(
        release_runtime_prestate,
        "_database_time",
        database_time,
    )
    receipt = release_runtime_prestate.capture_composite_prestate(
        repository_root=tmp_path,
        engine=object(),  # type: ignore[arg-type]
        review_record=tmp_path / "review.md",
        live_plan=plan,
        expected_sha="a" * 40,
        activation_nonce=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        team_slug="63amg0010-5358s-projects",
        org_id_env="VERCEL_ORG_ID",
        api_project_name="prediction-monitor-api",
        api_project_id_env="VERCEL_API_PROJECT_ID",
        web_project_name="prediction-monitor-web",
        web_project_id_env="VERCEL_WEB_PROJECT_ID",
        token_env="VERCEL_TOKEN",
        environ={
            "VERCEL_ORG_ID": "org",
            "VERCEL_API_PROJECT_ID": "api",
            "VERCEL_WEB_PROJECT_ID": "web",
            "VERCEL_TOKEN": "token",
        },
    )

    assert receipt["accepted"] is True
    assert [request.protected_ref for request in requests] == [
        "origin/main",
        "origin/main",
    ]
