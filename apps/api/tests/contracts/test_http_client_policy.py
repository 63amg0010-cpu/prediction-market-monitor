from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict


class _Project(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    dependencies: tuple[str, ...]


class _Manifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    project: _Project


REPOSITORY_ROOT: Final = Path(__file__).parents[4]
PRODUCTION_ROOTS: Final = (
    REPOSITORY_ROOT / "apps/api/app",
    REPOSITORY_ROOT / "workers/codex-worker/src",
)
MANIFESTS: Final = (
    REPOSITORY_ROOT / "apps/api/pyproject.toml",
    REPOSITORY_ROOT / "workers/codex-worker/pyproject.toml",
)


def test_production_does_not_import_original_httpx() -> None:
    violations: list[str] = []

    for source_root in PRODUCTION_ROOTS:
        for source_path in source_root.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            imports = (node for node in ast.walk(tree) if isinstance(node, ast.Import))
            imported_from = (
                node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            )
            if any(
                alias.name == "httpx" for node in imports for alias in node.names
            ) or any(node.module == "httpx" for node in imported_from):
                violations.append(str(source_path.relative_to(REPOSITORY_ROOT)))

    assert violations == []


def test_first_party_manifests_do_not_declare_original_httpx() -> None:
    violations: list[str] = []

    for manifest in MANIFESTS:
        parsed = _Manifest.model_validate(
            tomllib.loads(manifest.read_text(encoding="utf-8"))
        )
        if any(
            dependency.split("[", maxsplit=1)[0]
            .split("<", maxsplit=1)[0]
            .split(">", maxsplit=1)[0]
            == "httpx"
            for dependency in parsed.project.dependencies
        ):
            violations.append(str(manifest.relative_to(REPOSITORY_ROOT)))

    assert violations == []
