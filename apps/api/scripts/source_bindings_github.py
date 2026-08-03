"""GitHub CLI adapter for the exact production-collector target."""

# ruff: noqa: D101, D102, D103

from __future__ import annotations

import subprocess

from scripts.source_bindings_contracts import (
    TARGET_ARGS,
    CliError,
    GitHub,
    GitHubCommand,
)


class SubprocessGitHub:
    def execute(self, command: GitHubCommand) -> str:
        completed = subprocess.run(  # noqa: S603
            command.argv,
            input=command.stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise CliError(completed.stderr.strip() or "gh command failed")
        return completed.stdout.strip()


def get_variable(github: GitHub, name: str) -> str:
    return github.execute(
        GitHubCommand(
            (
                "gh",
                "variable",
                "get",
                name,
                *TARGET_ARGS,
                "--json",
                "value",
                "--jq",
                ".value",
            )
        )
    ).strip()


def set_secret(github: GitHub, value: str) -> None:
    _ = github.execute(
        GitHubCommand(
            (
                "gh",
                "secret",
                "set",
                "MONITOR_SOURCE_BINDINGS_JSON",
                *TARGET_ARGS,
            ),
            value,
        )
    )


def set_variable(github: GitHub, name: str, value: str) -> None:
    _ = github.execute(
        GitHubCommand(
            ("gh", "variable", "set", name, *TARGET_ARGS),
            value,
        )
    )


__all__ = ("SubprocessGitHub", "get_variable", "set_secret", "set_variable")
