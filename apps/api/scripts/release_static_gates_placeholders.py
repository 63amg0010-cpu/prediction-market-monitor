"""Production placeholder rejection for Todo 11 plan compliance."""

from __future__ import annotations

import ast
import io
import tokenize
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from scripts.release_static_gates_models import Finding
from scripts.release_static_gates_repo import walk_files

PRODUCTION_PATHS = (
    ".github",
    "apps/api/app",
    "apps/api/migrations",
    "apps/api/scripts",
    "apps/web/src",
    "config",
    "workers/codex-worker/src",
)


def scan_placeholders(root: Path) -> tuple[Finding, ...]:
    """Reject executable production placeholders, while ignoring tests."""
    findings: list[Finding] = []
    for relative, path in walk_files(root, PRODUCTION_PATHS):
        if path.suffix.lower() not in {".py", ".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".py":
            findings.extend(_python_placeholders(relative, text))
        else:
            findings.extend(_script_placeholders(relative, text))
    return tuple(findings)


def _script_placeholders(relative: str, text: str) -> list[Finding]:
    marker = "TO" + "DO"
    return [
        Finding("production_placeholder", relative, number)
        for number, line in enumerate(text.splitlines(), start=1)
        if line.lstrip().startswith(("//", "/*", "*")) and marker in line
    ]


def _python_placeholders(relative: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        marker = "TO" + "DO"
        findings.extend(
            Finding("production_placeholder", relative, token.start[0])
            for token in tokens
            if token.type == tokenize.COMMENT and marker in token.string
        )
    except (IndentationError, tokenize.TokenError):
        findings.append(Finding("invalid_python_syntax", relative))
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return findings
    findings.extend(
        Finding("production_stub", relative, node.lineno)
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Name)
            and node.exc.id == "NotImplementedError"
        )
    )
    findings.extend(
        Finding("production_stub", relative, line)
        for line in concrete_stub_lines(tree)
    )
    return findings


def concrete_stub_lines(tree: ast.AST) -> tuple[int, ...]:
    """Return only executable stubs, excluding Protocol/abstract declarations."""
    declarations: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            _name(base) == "Protocol" for base in node.bases
        ):
            declarations.update(
                id(child)
                for child in ast.walk(node)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    return tuple(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and id(node) not in declarations
        and not any(
            _name(decorator) in {"abstractmethod", "overload"}
            for decorator in node.decorator_list
        )
        and _stub_body(node.body)
    )


def _stub_body(body: list[ast.stmt]) -> bool:
    executable = [
        node
        for node in body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    return len(executable) == 1 and _is_stub(executable[0])


def _is_stub(node: ast.stmt) -> bool:
    return isinstance(node, ast.Pass) or (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and node.value.value is Ellipsis
    )


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""
