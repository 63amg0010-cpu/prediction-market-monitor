"""Fail-closed child invocation and result completeness for local QA."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from scripts.local_qa_manifest import OPENAPI_COMMAND, Argv

if TYPE_CHECKING:
    from pathlib import Path

type Executor = Callable[[str, Argv, dict[str, str]], int]
INJECTED_EXIT_CODE: Final = 97
OUTPUT_CHARACTER_LIMIT: Final = 65_536
REPLACEMENT_CHARACTER_LIMIT: Final = 4_096
UNEXPECTED_EXIT_CODE: Final = 2
EXPECTED_LABELS: Final = (
    "provision",
    *(f"command-{number:02d}" for number in range(1, 21)),
    "dispose",
)
DATABASE_LABELS: Final = frozenset(
    {"provision", "command-09", "command-10", "dispose"}
)
ADMIN_LABELS: Final = frozenset({"provision", "dispose"})


@dataclass(frozen=True, slots=True)
class Invocation:
    """One redacted child result, including unexpected executor failures."""

    exit_code: int
    event: dict[str, object]


@dataclass(frozen=True, slots=True)
class Options:
    """Closed wrapper-neutral orchestrator inputs."""

    attempt_dir: Path
    admin_env: str
    database_env: str
    base_sha: str
    reviewed_sha: str
    failure_fixture: Path | None
    expect_meta_failure: bool
    wrapper: str


@dataclass(frozen=True, slots=True)
class EnvironmentScope:
    """Project database credentials into only exact lifecycle children."""

    base: dict[str, str]
    database_name: str
    database_value: str
    admin_name: str
    admin_value: str

    def for_label(self, label: str) -> dict[str, str]:
        """Return a fresh non-leaking environment for one invocation."""
        environment = self.base.copy()
        for name in {
            "DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            self.database_name,
            self.admin_name,
        }:
            _ = environment.pop(name, None)
        if label in DATABASE_LABELS:
            environment[self.database_name] = self.database_value
        if label in ADMIN_LABELS and self.admin_value:
            environment[self.admin_name] = self.admin_value
        return environment


def invoke(
    executor: Executor,
    label: str,
    argv: Argv,
    environment: dict[str, str],
) -> Invocation:
    """Convert every executor exception into a stable failed event."""
    try:
        code = executor(label, argv, environment)
    except Exception:  # noqa: BLE001 - trust boundary must become a failed receipt.
        return unexpected_invocation(label)
    return Invocation(
        exit_code=code,
        event={"label": label, "exit_code": code, "accepted": code == 0},
    )


def injected_executor(point: str, calls: list[str]) -> Executor:
    """Return a deterministic executor for meta-failure coverage."""

    def execute(label: str, argv: Argv, env: dict[str, str]) -> int:
        del argv, env
        calls.append(label)
        return INJECTED_EXIT_CODE if label == point else 0

    return execute


def unexpected_invocation(label: str) -> Invocation:
    """Build the sole schema-safe unexpected failure representation."""
    return Invocation(
        exit_code=UNEXPECTED_EXIT_CODE,
        event={
            "label": label,
            "exit_code": UNEXPECTED_EXIT_CODE,
            "accepted": False,
            "failure_code": "child_executor_exception",
        },
    )


def final_exit_code(code: int, events: list[dict[str, object]]) -> int:
    """Refuse success unless every exact ordered invocation accepted."""
    labels = tuple(event.get("label") for event in events)
    accepted = all(event.get("accepted") is True for event in events)
    if code == 0 and labels == EXPECTED_LABELS and accepted:
        return 0
    return code if code != 0 else UNEXPECTED_EXIT_CODE


def redact_output(value: str | None, environment: dict[str, str]) -> str:
    """Require decoded text and remove database/credential values before output."""
    if value is None:
        error_code = "child_output_decode_failed"
        raise RuntimeError(error_code)
    if value.count("\ufffd") > REPLACEMENT_CHARACTER_LIMIT:
        error_code = "child_output_replacement_limit_exceeded"
        raise RuntimeError(error_code)
    redacted = value
    sensitive_markers = (
        "DATABASE_URL",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
        "PRIVATE_KEY",
    )
    for name, secret in environment.items():
        if secret and any(marker in name.upper() for marker in sensitive_markers):
            redacted = redacted.replace(secret, "[REDACTED]")
    if len(redacted) > OUTPUT_CHARACTER_LIMIT:
        marker = "\n[output truncated]\n"
        redacted = redacted[: OUTPUT_CHARACTER_LIMIT - len(marker)] + marker
    return redacted


def console_safe_text(value: str, encoding: str | None) -> str:
    """Escape only characters the host diagnostic console cannot encode."""
    selected = encoding or "utf-8"
    try:
        return value.encode(selected, errors="backslashreplace").decode(selected)
    except LookupError:
        return value.encode("utf-8", errors="backslashreplace").decode("utf-8")


def runtime_working_directory(label: str, argv: Argv, root: Path) -> Path:
    """Project only the exact OpenAPI command into the API package directory."""
    root_resolved = root.resolve()
    if label != "command-11":
        return root_resolved
    if argv != OPENAPI_COMMAND:
        error_code = "openapi_command_contract_invalid"
        raise RuntimeError(error_code)
    expected = root_resolved / "apps" / "api"
    resolved = expected.resolve()
    if resolved != expected or not resolved.is_dir():
        error_code = "openapi_working_directory_invalid"
        raise RuntimeError(error_code)
    return resolved


def write_json(path: Path, body: dict[str, object]) -> None:
    """Write a canonical UTF-8 receipt with an integrity digest."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    output = {**body, "receipt_sha256": hashlib.sha256(canonical).hexdigest()}
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = (
    "ADMIN_LABELS",
    "DATABASE_LABELS",
    "EXPECTED_LABELS",
    "INJECTED_EXIT_CODE",
    "OUTPUT_CHARACTER_LIMIT",
    "REPLACEMENT_CHARACTER_LIMIT",
    "EnvironmentScope",
    "Executor",
    "Invocation",
    "Options",
    "console_safe_text",
    "final_exit_code",
    "injected_executor",
    "invoke",
    "redact_output",
    "runtime_working_directory",
    "unexpected_invocation",
    "write_json",
)
