from __future__ import annotations

# ruff: noqa: E402
# pyright: reportImplicitStringConcatenation=false
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_runtime_retention import AliasRetentionRuntime
from scripts.release_vercel_models import (
    TEAM_SLUG,
    ChildCommand,
    ChildResult,
)
from scripts.release_vercel_retention import EVIDENCE_SOURCE

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
SHA = "a" * 40


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[ChildCommand] = []

    def execute(self, command: ChildCommand) -> ChildResult:
        self.commands.append(command)
        if command.stage == "alias-ls":
            return ChildResult(
                0,
                '{"aliases":[{"alias":"prediction-monitor-api-fresh-search-'
                'compat.vercel.app","deploymentId":"dpl_api"}]}',
            )
        return ChildResult(
            0,
            '{"id":"dpl_api","name":"prediction-monitor-api",'
            f'"team":"{TEAM_SLUG}","target":"production",'
            f'"readyState":"READY","meta":{{"githubCommitSha":"{SHA}"}}}}',
        )


def test_retention_runtime_uses_current_alias_ls_and_inspect(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    runtime = AliasRetentionRuntime(tmp_path, runner=runner)
    alias = "prediction-monitor-api-fresh-search-compat.vercel.app"
    observation = runtime.observe(
        "api",
        {
            "alias": alias,
            "deployment_id": "dpl_api",
        },
        NOW,
    )

    assert [command.stage for command in runner.commands] == [
        "alias-ls",
        "inspect",
    ]
    assert runner.commands[0].argv == (
        "npx",
        "--yes",
        "vercel@51.7.0",
        "alias",
        "ls",
        "--scope",
        TEAM_SLUG,
        "--json",
    )
    assert runner.commands[1].argv[3:5] == ("inspect", alias)
    assert runner.commands[0].env == {
        "VERCEL_ORG_ID_FROM_ENV": "VERCEL_ORG_ID",
        "VERCEL_PROJECT_ID_FROM_ENV": "VERCEL_API_PROJECT_ID",
        "VERCEL_TOKEN_FROM_ENV": "VERCEL_TOKEN",
    }
    assert observation.observed_at == NOW
    assert observation.evidence_source == EVIDENCE_SOURCE
