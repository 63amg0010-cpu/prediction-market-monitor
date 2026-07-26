from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_vercelignore_preserves_generated_python_function_dependencies() -> None:
    # Given: Vercel packages Python dependencies in a nested generated .venv.
    rules = {
        line.strip()
        for line in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    # When/Then: only the repository-root development environment is ignored.
    assert "/.venv/" in rules
    assert ".venv/" not in rules
    assert ".vercel/" not in rules
    assert "/.vercel/" not in rules
