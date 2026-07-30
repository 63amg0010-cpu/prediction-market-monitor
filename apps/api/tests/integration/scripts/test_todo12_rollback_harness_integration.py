from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from scripts.release_rollback_harness import Options, run_harness
from scripts.release_rollback_harness_db import RealDatabase

if TYPE_CHECKING:
    from pathlib import Path


def test_real_disposable_postgres_0010_0011_0010(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in hook for the guarded local PostgreSQL release rehearsal."""
    if os.environ.get("RUN_TODO12_ROLLBACK_INTEGRATION") != "1":
        pytest.skip("set RUN_TODO12_ROLLBACK_INTEGRATION=1 on guarded DB0010")
    url = os.environ.get("MIGRATION_QA_DATABASE_URL")
    if not url:
        pytest.fail("MIGRATION_QA_DATABASE_URL is required")
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", url)

    receipt = run_harness(
        Options(
            mode="disposable",
            database_url_env="MIGRATION_QA_DATABASE_URL",
            stub_external=True,
            expected_sha="a" * 40,
            json_out=tmp_path / "rollback-harness.json",
        ),
        database=RealDatabase(),
    )

    assert receipt["accepted"] is True
    assert receipt["database"]["revision_after"] == "20260727_0010"
    assert receipt["database"]["manifold_enabled"] is False
    assert receipt["database"]["manifold_pointers_null"] is True
    assert receipt["database"]["dcinside_preserved"] is True
    assert receipt["external"]["executed_count"] == 0
