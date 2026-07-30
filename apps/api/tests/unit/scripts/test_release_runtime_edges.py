from __future__ import annotations

# ruff: noqa: ANN401, S105, S106
# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false
# pyright: reportUnannotatedClassAttribute=false, reportUnusedCallResult=false
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from app.services.release.receipts import canonicalize
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
from scripts.release_vercel_models import ChildCommand

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
