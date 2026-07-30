from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, cast

import anyio
from anyio.to_thread import run_sync as run_sync_in_worker_thread

if TYPE_CHECKING:
    from pathlib import Path
from scripts.release_static_gates import (
    CodeQualityRequest,
    LinksRequest,
    PlanComplianceRequest,
    ScopeFidelityRequest,
    SecretScanRequest,
    run_code_quality,
    run_links,
    run_plan_compliance,
    run_scope_fidelity,
    run_secret_static_scan,
)

BASE_SHA = "a" * 40
REVIEWED_SHA = "b" * 40


async def _git_process(root: Path, arguments: tuple[str, ...]) -> bytes:
    completed = await anyio.run_process(
        ("git", *arguments),
        cwd=root,
        check=True,
    )
    return completed.stdout


def _process_loop_factory() -> asyncio.AbstractEventLoop:
    loop_type = cast(
        "type[asyncio.AbstractEventLoop]",
        getattr(asyncio, "ProactorEventLoop", asyncio.SelectorEventLoop),
    )
    return loop_type()


def _git_in_process_loop(root: Path, arguments: tuple[str, ...]) -> bytes:
    runner = asyncio.Runner(loop_factory=_process_loop_factory)
    with runner:
        local_loop = runner.get_loop()
        result = runner.run(_git_process(root, arguments))
    assert local_loop.is_closed()
    return result


async def _git_in_worker(root: Path, arguments: tuple[str, ...]) -> bytes:
    return await run_sync_in_worker_thread(_git_in_process_loop, root, arguments)


def _git(root: Path, *arguments: str) -> str:
    runner = asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)
    with runner:
        local_loop = runner.get_loop()
        raw = runner.run(_git_in_worker(root, arguments))
    assert local_loop.is_closed()
    return raw.decode().strip()


def test_git_runner_preserves_callers_current_loop(tmp_path: Path) -> None:
    root = tmp_path / "loop-repo"
    root.mkdir()

    def exercise_in_isolated_thread() -> None:
        sentinel = asyncio.new_event_loop()
        asyncio.set_event_loop(sentinel)
        try:
            _ = _git(root, "init", "-q")

            assert asyncio.get_event_loop() is sentinel
            assert not sentinel.is_closed()
        finally:
            asyncio.set_event_loop(None)
            sentinel.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(exercise_in_isolated_thread).result()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _ = _git(root, "init", "-q")
    _ = _git(root, "config", "user.email", "release-gate@example.invalid")
    _ = _git(root, "config", "user.name", "Release Gate")
    _ = (root / "README.md").write_text("# Home\n", encoding="utf-8")
    _ = _git(root, "add", "README.md")
    _ = _git(root, "commit", "-qm", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _commit(root: Path, path: str, content: str | bytes) -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        _ = target.write_bytes(content)
    else:
        _ = target.write_text(content, encoding="utf-8")
    _ = _git(root, "add", path)
    _ = _git(root, "commit", "-qm", "reviewed")
    return _git(root, "rev-parse", "HEAD")


def _document(path: Path) -> dict[str, object]:
    raw = cast("object", json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(raw, dict)
    mapping = cast("dict[object, object]", raw)
    assert all(isinstance(key, str) for key in mapping)
    return {str(key): value for key, value in mapping.items()}


def _finding_codes(document: dict[str, object]) -> set[str]:
    raw = document["findings"]
    assert isinstance(raw, list)
    findings = cast("list[object]", raw)
    codes: set[str] = set()
    for item in findings:
        assert isinstance(item, dict)
        finding = cast("dict[object, object]", item)
        code = finding.get("code")
        assert isinstance(code, str)
        codes.add(code)
    return codes


def test_secret_gate_preserves_callers_current_loop(tmp_path: Path) -> None:
    root, base_sha = _repository(tmp_path)
    reviewed_sha = _commit(root, "safe.py", "VALUE: int = 1\n")
    output = root / "attempt" / "secret-scan.json"

    def exercise_in_isolated_thread() -> None:
        sentinel = asyncio.new_event_loop()
        asyncio.set_event_loop(sentinel)
        try:
            assert (
                run_secret_static_scan(
                    SecretScanRequest(root, base_sha, reviewed_sha, output)
                )
                == 0
            )
            assert asyncio.get_event_loop() is sentinel
            assert not sentinel.is_closed()
        finally:
            asyncio.set_event_loop(None)
            sentinel.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(exercise_in_isolated_thread).result()


def test_secret_scan_is_changed_path_scoped_and_redacts_secret(
    tmp_path: Path,
) -> None:
    root, base_sha = _repository(tmp_path)
    token_prefix = bytes((103, 104, 112, 95)).decode()
    assignment_name = bytes((84, 79, 75, 69, 78)).decode()
    reviewed_sha = _commit(
        root,
        "apps/api/app/leak.py",
        f'{assignment_name} = "{token_prefix}abcdefghijklmnopqrstuvwxyz1234567890"\n',
    )
    output = root / "attempt" / "secret-scan.json"

    exit_code = run_secret_static_scan(
        SecretScanRequest(root, base_sha, reviewed_sha, output)
    )

    document = _document(output)
    assert exit_code == 2
    assert document["accepted"] is False
    assert _finding_codes(document) == {"secret_literal"}
    assert token_prefix not in output.read_text(encoding="utf-8")


def test_secret_scan_rejects_plaintext_capture_and_magic_bytes(
    tmp_path: Path,
) -> None:
    root, base_sha = _repository(tmp_path)
    reviewed_sha = _commit(root, "captures/billing-dump.bin", b"%PDF-1.7 secret")
    output = root / "attempt" / "secret-scan.json"

    exit_code = run_secret_static_scan(
        SecretScanRequest(root, base_sha, reviewed_sha, output)
    )

    codes = _finding_codes(_document(output))
    assert exit_code == 2
    assert codes == {"forbidden_magic_bytes", "plaintext_dump_path"}


def test_code_quality_rejects_suppressions_and_python_stubs(tmp_path: Path) -> None:
    root, base_sha = _repository(tmp_path)
    type_marker = "# type:"
    source = "\n".join(
        (
            "def unfinished(value: str) -> str:",
            f"    pass  {type_marker} ignore[return-value]",
            "",
        )
    )
    reviewed_sha = _commit(
        root,
        "apps/api/app/stub.py",
        source,
    )
    output = root / "attempt" / "quality.md"
    output.parent.mkdir()

    exit_code = run_code_quality(
        CodeQualityRequest(root, base_sha, reviewed_sha, root / "attempt", output)
    )

    report = output.read_text(encoding="utf-8")
    assert exit_code == 2
    assert report.startswith("REJECT\n")
    assert "python_stub" in report
    assert "type_suppression" in report


def test_scope_fidelity_rejects_out_of_scope_service_and_raw_persistence(
    tmp_path: Path,
) -> None:
    root, base_sha = _repository(tmp_path)
    reviewed_sha = _commit(
        root,
        "terraform/new-service.sql",
        "ALTER TABLE posts ADD COLUMN raw_body text;\n",
    )
    output = root / "attempt" / "scope.json"

    exit_code = run_scope_fidelity(
        ScopeFidelityRequest(
            root=root,
            json_out=output,
            base_sha=base_sha,
            reviewed_sha=reviewed_sha,
        )
    )

    codes = _finding_codes(_document(output))
    assert exit_code == 2
    assert codes == {"out_of_scope_path", "raw_identity_persistence"}


def test_links_validates_relative_targets_and_markdown_anchors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    docs = root / "docs"
    docs.mkdir(parents=True)
    _ = (root / "README.md").write_text(
        "# Home\n[Guide](docs/guide.md#safe-operation)\n",
        encoding="utf-8",
    )
    _ = (docs / "guide.md").write_text("# Safe operation\n", encoding="utf-8")
    output = root / "attempt" / "links.json"

    exit_code = run_links(LinksRequest(root, ("README.md", "docs"), output))

    document = _document(output)
    assert exit_code == 0
    assert document["accepted"] is True
    assert document["checked_links"] == 1


def test_links_fails_closed_for_missing_target_and_anchor(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _ = (root / "README.md").write_text(
        "[Missing](docs/nope.md)\n[Anchor](README.md#absent)\n",
        encoding="utf-8",
    )
    output = root / "attempt" / "links.json"

    exit_code = run_links(LinksRequest(root, ("README.md",), output))

    codes = _finding_codes(_document(output))
    assert exit_code == 2
    assert codes == {"missing_link_anchor", "missing_link_target"}


def test_local_plan_compliance_rejects_todo_and_stub_in_production(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    source = root / "apps" / "api" / "app"
    source.mkdir(parents=True)
    _ = (source / "unfinished.py").write_text(
        "# TODO: replace fixture\nraise NotImplementedError\n",
        encoding="utf-8",
    )
    output = root / "attempt" / "plan.json"

    exit_code = run_plan_compliance(
        PlanComplianceRequest(root=root, json_out=output)
    )

    codes = _finding_codes(_document(output))
    assert exit_code == 2
    assert codes == {"production_placeholder", "production_stub"}


def test_final_plan_compliance_requires_all_todo_evidence(tmp_path: Path) -> None:
    root, base_sha = _repository(tmp_path)
    plan = root / "plan.md"
    _ = plan.write_text(
        "\n".join(f"- [x] {number}. done" for number in range(1, 13))
    )
    reviewed_sha = _commit(root, "safe.py", "VALUE: int = 1\n")
    evidence_dir = root / "attempt"
    evidence_dir.mkdir()
    production_result = (
        evidence_dir / "task-12-fresh-multi-source-search" / "result.json"
    )
    production_result.parent.mkdir()
    _ = production_result.write_text(
        json.dumps(
            {
                "accepted": True,
                "redacted": True,
                "reviewed_sha": reviewed_sha,
                "revision": "20260727_0011",
                "receipt_sha256": "c" * 64,
            }
        ),
        encoding="utf-8",
    )
    output = evidence_dir / "final-F1.md"

    exit_code = run_plan_compliance(
        PlanComplianceRequest(
            root=root,
            plan=plan,
            base_sha=base_sha,
            reviewed_sha=reviewed_sha,
            evidence_dir=evidence_dir,
            production_result=production_result,
            expected_revision="20260727_0011",
            output=output,
        )
    )

    report = output.read_text(encoding="utf-8")
    assert exit_code == 2
    assert report.startswith("REJECT\n")
    assert "missing_todo_evidence" in report
    assert reviewed_sha in report


def test_json_output_is_byte_stable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _ = (root / "README.md").write_text("# Home\n", encoding="utf-8")
    first = root / "first.json"
    second = root / "second.json"

    assert run_links(LinksRequest(root, ("README.md",), first)) == 0
    assert run_links(LinksRequest(root, ("README.md",), second)) == 0

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
