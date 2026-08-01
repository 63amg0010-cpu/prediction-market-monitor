"""Pure, non-executable rendering for the disposable Matrix-B drill."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from scripts.release_rollback_harness_models import MatrixCommand

REPOSITORY: Final = "63amg0010-cpu/prediction-market-monitor"
TEAM: Final = "63amg0010-5358s-projects"
CLI: Final = ("npx", "--yes", "vercel@51.7.0")
PLAN_SHA: Final = "0" * 64
NONCE: Final = "00000000-0000-4000-8000-000000000000"


def _command(stage: str, argv: tuple[str, ...]) -> MatrixCommand:
    return {"argv": list(argv), "cwd": ".", "stage": stage}


def _vercel(project: str, expected_sha: str) -> list[MatrixCommand]:
    name = f"prediction-monitor-{project}"
    worktree = f".rollback-harness/{project}-worktree"
    cwd = f"{worktree}/apps/{project}"
    base = (*CLI,)
    fixed = ("--scope", TEAM, "--yes")
    url = f"https://{name}-stub.vercel.app"
    alias = f"{name}.vercel.app"
    stages = (
        (
            "target-sha",
            ("git", "rev-parse", "--verify", f"{expected_sha}^{{commit}}"),
        ),
        (
            "protected-sha",
            ("git", "rev-parse", "--verify", "origin/main^{commit}"),
        ),
        (
            "reachable",
            ("git", "merge-base", "--is-ancestor", expected_sha, "origin/main"),
        ),
        (
            "worktree-add",
            ("git", "worktree", "add", "--detach", worktree, expected_sha),
        ),
        (
            "pull",
            (*base, "pull", "--environment=production", *fixed),
        ),
        ("build", (*base, "build", "--prod", *fixed)),
        ("deploy", (*base, "deploy", "--prebuilt", "--prod", *fixed)),
        ("inspect", (*base, "inspect", url, "--scope", TEAM, "--json")),
        ("alias", (*base, "alias", "set", url, alias, "--scope", TEAM)),
        ("health", (*base, "curl", f"https://{alias}/health", "--scope", TEAM)),
        (
            "worktree-remove",
            ("git", "worktree", "remove", "--force", worktree),
        ),
    )
    return [
        {
            **_command(f"{project}-{stage}", argv),
            "cwd": "." if stage in {
                "target-sha",
                "protected-sha",
                "reachable",
                "worktree-add",
                "worktree-remove",
            } else cwd,
        }
        for stage, argv in stages
    ]


def render_matrix_b(expected_sha: str) -> list[MatrixCommand]:
    """Return exact command arrays; callers may record but never execute them."""
    downgrade = (
        "gh",
        "workflow",
        "run",
        "migrate.yml",
        "--repo",
        REPOSITORY,
        "--ref",
        "main",
        "-f",
        "operation=downgrade",
        "-f",
        "revision=20260727_0010",
        "-f",
        "confirm=migrate-production",
        "-f",
        "attempt=1",
        "-f",
        f"expected_commit_sha={expected_sha}",
        "-f",
        f"expected_plan_sha256={PLAN_SHA}",
        "-f",
        f"activation_nonce={NONCE}",
        "-f",
        f"dispatch_nonce={NONCE}",
    )
    binding_base = (
        "uv",
        "run",
        "--package",
        "monitor-api",
        "python",
        "apps/api/scripts/source_bindings.py",
    )
    restore = (
        *binding_base,
        "restore-github",
        "--activation-nonce",
        NONCE,
        "--database-url-env",
        "MIGRATION_DATABASE_URL",
        "--predecessor-receipt",
        ".rollback-harness/downgrade.json",
        "--payload-receipt",
        ".rollback-harness/zero-provider.json",
        "--prestate-receipt",
        ".rollback-harness/binding-prestate.json",
        "--json-out",
        ".rollback-harness/binding-restore.json",
    )
    verify = (
        *binding_base,
        "verify-github",
        "--activation-nonce",
        NONCE,
        "--database-url-env",
        "MIGRATION_DATABASE_URL",
        "--payload-receipt",
        ".rollback-harness/zero-provider.json",
        "--collection-receipt",
        ".rollback-harness/zero-provider-verification.json",
        "--json-out",
        ".rollback-harness/binding-restore-verified.json",
    )
    return [
        _command("downgrade-workflow", downgrade),
        _command("binding-restore", restore),
        _command("binding-restore-verify", verify),
        *_vercel("api", expected_sha),
        *_vercel("web", expected_sha),
    ]


__all__ = ("render_matrix_b",)
