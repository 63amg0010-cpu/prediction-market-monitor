"""Fail-closed content scans for Todo 11 release reports."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.release_static_gates_models import Finding
from scripts.release_static_gates_placeholders import concrete_stub_lines
from scripts.release_static_gates_repo import read_changed_file, walk_files

TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".html",
        ".json",
        ".md",
        ".py",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
QUALITY_SUFFIXES = frozenset({".py", ".sql", ".ts", ".tsx", ".yaml", ".yml"})
PRODUCTION_PREFIXES = (
    ".github/",
    "apps/api/app/",
    "apps/api/migrations/",
    "apps/api/scripts/",
    "apps/web/src/",
    "config/",
    "workers/codex-worker/src/",
)
ALLOWED_SCOPE_PREFIXES = (
    ".github/",
    ".omo/",
    "apps/",
    "config/",
    "contracts/",
    "docs/",
    "scripts/",
    "tools/",
    "workers/",
)
ALLOWED_SCOPE_FILES = frozenset(
    {
        ".dockerignore",
        ".env.example",
        ".gitattributes",
        ".gitignore",
        ".gitleaksignore",
        ".node-version",
        ".python-version",
        ".vercelignore",
        "DESIGN.md",
        "README.md",
        "docker-compose.yml",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "pyproject.toml",
        "uv.lock",
    }
)
LITERAL_ASSIGNMENT_PREFIX = (
    r"(?i)\b(?:token|secret|password|api[_-]?key)\s*[:=]\s*"
)
LITERAL_ASSIGNMENT_VALUE = r"['\"][^'\"\s]{12,}['\"]"
SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:sk|pk)_live_[A-Za-z0-9]{16,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bpostgres(?:ql)?://[^:\s/]+:[^@\s/]+@"),
    re.compile(f"{LITERAL_ASSIGNMENT_PREFIX}{LITERAL_ASSIGNMENT_VALUE}"),
)
TYPE_SUPPRESSIONS = (
    re.compile(r"#\s*(?:type:\s*ignore|noqa|pyright:\s*ignore|nosec)\b"),
    re.compile(r"(?://|/\*)\s*(?:@ts-ignore|eslint-disable)\b"),
    re.compile(r"\bas\s+any\b"),
)
PRIVACY_SENTINELS = re.compile(
    r'(?i)["\'](?:user_?id|creator_?id|author_?id|raw_?(?:body|payload))["\']'
)
RAW_PERSISTENCE = re.compile(
    r"(?i)\b(?:raw_?(?:body|payload)|author_?id|creator_?id|user_?id)\b"
)
PAID_MARKER_PREFIX = (
    r"(?i)\b(?:paid_plan|billing_enabled|overage_enabled|plan\s*[:=]\s*"
)
PAID_MARKER_VALUE = r"['\"](?:pro|team|enterprise)['\"])\b"
PAID_MARKERS = re.compile(f"{PAID_MARKER_PREFIX}{PAID_MARKER_VALUE}")
MAGIC_BYTES = (b"%PDF-", b"PK\x03\x04", b"\x89PNG\r\n\x1a\n", b"SQLite format 3\x00")
DUMP_WORDS = re.compile(
    r"(?i)(?:^|[-_.])(?:dump|screenshot|dom|backup|capture)(?:[-_.]|$)"
)


def scan_secrets(root: Path, paths: tuple[str, ...]) -> tuple[Finding, ...]:
    """Scan changed files without ever returning matched content."""
    findings: list[Finding] = []
    for relative in paths:
        findings.extend(_secret_file_findings(root, relative))
    return tuple(findings)


def scan_code_quality(root: Path, paths: tuple[str, ...]) -> tuple[Finding, ...]:
    """Inspect changed typed source boundaries for high-signal violations."""
    findings: list[Finding] = []
    for relative in paths:
        suffix = Path(relative).suffix.lower()
        if suffix not in QUALITY_SUFFIXES:
            continue
        try:
            text = read_changed_file(root, relative).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            findings.append(Finding("unscannable_quality_file", relative))
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in TYPE_SUPPRESSIONS):
                findings.append(Finding("type_suppression", relative, line_number))
            if suffix in {".sql", ".ts", ".tsx"} and _interpolated_sql(line):
                findings.append(Finding("interpolated_sql", relative, line_number))
        if suffix == ".py" and _production_path(relative):
            findings.extend(_python_quality(relative, text))
    manifold_changed = any("adapters/manifold" in item for item in paths)
    if manifold_changed:
        findings.extend(_manifold_bounds(root))
    return tuple(findings)


def scan_scope(root: Path, paths: tuple[str, ...]) -> tuple[Finding, ...]:
    """Reject paths and persistence changes outside the reviewed source scope."""
    findings: list[Finding] = []
    for relative in paths:
        if relative not in ALLOWED_SCOPE_FILES and not relative.startswith(
            ALLOWED_SCOPE_PREFIXES
        ):
            findings.append(Finding("out_of_scope_path", relative))
        try:
            text = read_changed_file(root, relative).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        if _production_path(relative) or Path(relative).suffix.lower() == ".sql":
            for line_number, line in enumerate(text.splitlines(), start=1):
                if RAW_PERSISTENCE.search(line):
                    findings.append(
                        Finding("raw_identity_persistence", relative, line_number)
                    )
                if PAID_MARKERS.search(line):
                    findings.append(
                        Finding("paid_service_marker", relative, line_number)
                    )
        if "adapters/dcinside" in relative.replace("\\", "/"):
            findings.append(Finding("dcinside_scope_drift", relative))
    return tuple(findings)


def _python_quality(relative: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [Finding("invalid_python_syntax", relative, error.lineno or 0)]
    findings.extend(
        Finding("python_stub", relative, line) for line in concrete_stub_lines(tree)
    )
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.returns is None
            or any(arg.annotation is None for arg in node.args.args)
        ):
            findings.append(Finding("untyped_boundary", relative, node.lineno))
        if isinstance(node, ast.JoinedStr) and _sql_text(ast.unparse(node)):
            findings.append(Finding("interpolated_sql", relative, node.lineno))
    return findings


def _manifold_bounds(root: Path) -> list[Finding]:
    files = walk_files(root, ("apps/api/app/collection/adapters",))
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for relative, path in files
        if "manifold" in relative and path.suffix == ".py"
    )
    findings: list[Finding] = []
    if re.search(r"\b21\b", combined) is None:
        findings.append(Finding("missing_21_request_bound", "apps/api/app"))
    if not any(value in combined for value in ("262_144", "262144", "256 * 1024")):
        findings.append(Finding("missing_256kib_bound", "apps/api/app"))
    return findings


def _secret_file_findings(root: Path, relative: str) -> list[Finding]:
    try:
        raw = read_changed_file(root, relative)
    except ValueError:
        return [Finding("unscannable_changed_file", relative)]
    findings: list[Finding] = []
    risky_path = DUMP_WORDS.search(relative) is not None
    if risky_path and not _encrypted_or_redacted(relative):
        findings.append(Finding("plaintext_dump_path", relative))
    if any(raw.startswith(magic) for magic in MAGIC_BYTES) and (
        risky_path or not _allowed_asset(relative)
    ):
        findings.append(Finding("forbidden_magic_bytes", relative))
    if Path(relative).suffix.lower() not in TEXT_SUFFIXES:
        return findings
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [*findings, Finding("non_utf8_text", relative)]
    for line_number, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            findings.append(Finding("secret_literal", relative, line_number))
        if _artifact_like(relative) and PRIVACY_SENTINELS.search(line):
            findings.append(
                Finding("forbidden_privacy_sentinel", relative, line_number)
            )
    return findings


def _interpolated_sql(line: str) -> bool:
    has_interpolation = "${" in line or re.search(
        r"\b(?:f|F)['\"]",
        line,
    ) is not None
    return has_interpolation and _sql_text(line)


def _sql_text(value: str) -> bool:
    return (
        re.search(
            r"(?i)\b(?:select|insert|update|delete|alter|create)\b",
            value,
        )
        is not None
    )


def _production_path(relative: str) -> bool:
    return relative.startswith(PRODUCTION_PREFIXES) and "/tests/" not in relative


def _artifact_like(relative: str) -> bool:
    return relative.startswith((".omo/evidence/", "docs/evidence/", "captures/"))


def _encrypted_or_redacted(relative: str) -> bool:
    path = Path(relative)
    encrypted = path.suffix.lower() in {".age", ".enc", ".gpg"}
    return encrypted or "redacted" in path.name.lower()


def _allowed_asset(relative: str) -> bool:
    return relative.startswith(("apps/web/public/", "apps/web/src/assets/"))
