"""Acquire private GitHub/Vercel quota payloads without terminal JSON."""

# pyright: reportAny=false, reportArgumentType=false, reportReturnType=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnnecessaryComparison=false, reportUnusedCallResult=false
# ruff: noqa: BLE001, C901, D107, EM101, EM102, FURB162, PLR0912, PLR0915, PLR2004, PTH100, S603, T201, TC002, TRY301

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Final, cast

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apps.api.scripts.free_tier_captures import (
    windows_acl_owner_only as _windows_acl_owner_only,
)
from apps.api.scripts.free_tier_domain import JsonObject, JsonValue
from apps.api.scripts.private_windows_directory_lock import hold_private_directory

REPOSITORY: Final = "63amg0010-cpu/prediction-market-monitor"
OWNER: Final = "63amg0010-cpu"
TEAM_SLUG: Final = "63amg0010-5358s-projects"
SCHEMA: Final = "free-tier.provider-official-payloads.v1"
SUPPORTED_PROVIDERS: Final = frozenset({"github", "vercel-api", "vercel-web"})
GITHUB_ITEM_FIELDS: Final = frozenset(
    {
        "product",
        "sku",
        "unitType",
        "pricePerUnit",
        "grossQuantity",
        "grossAmount",
        "discountQuantity",
        "discountAmount",
        "netQuantity",
        "netAmount",
    }
)
GITHUB_USAGE_TUPLES: Final = {
    ("Actions", "actions_linux", "minutes"): "github_actions_minutes",
    ("Actions", "actions_windows", "minutes"): "github_actions_minutes",
    ("Actions", "actions_macos", "minutes"): "github_actions_minutes",
    ("Actions", "actions_storage", "gigabyte-hours"): "github_artifact_gb_hours",
    ("Packages", "packages_storage", "gigabyte-hours"): "github_packages_gb_hours",
}
GITHUB_REQUIRED_BILLING_DIMENSIONS: Final = frozenset(
    {
        "github_actions_minutes",
        "github_artifact_gb_hours",
        "github_packages_gb_hours",
    }
)
FOCUS_FIELDS: Final = frozenset(
    {
        "BillingAccountId",
        "ChargePeriodStart",
        "ChargePeriodEnd",
        "ResourceId",
        "ResourceName",
        "ServiceName",
        "SkuId",
        "SkuPriceId",
        "ConsumedQuantity",
        "ConsumedUnit",
        "BilledCost",
        "BillingCurrency",
    }
)
VERCEL_FOCUS_TUPLES: Final = {
    "vercel-api": {
        ("Functions", "function_invocations", "invocations"): "vercel_api_invocations",
    },
    "vercel-web": {
        ("Functions", "function_invocations", "invocations"): "vercel_web_invocations",
        ("Functions", "function_cpu", "milliseconds"): "vercel_cpu_ms",
        (
            "Functions",
            "function_memory",
            "gigabyte-seconds",
        ): "vercel_memory_gb_seconds",
        ("Data Transfer", "edge_transfer", "bytes"): "vercel_transfer_bytes",
        ("Builds", "deployments", "deployments"): "vercel_deployments",
    },
}
MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
MAX_AGGREGATE_BYTES: Final = 8 * 1024 * 1024
TIMEOUT: Final = httpx2.Timeout(10.0, connect=10.0, read=10.0, write=10.0, pool=10.0)
LIMITS: Final = httpx2.Limits(max_connections=2, max_keepalive_connections=1)


class CaptureHoldError(RuntimeError):
    """Fail-closed acquisition error carrying only an allowlisted code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    """Validated acquisition inputs."""

    provider: str
    token: str
    identity_envs: tuple[str, ...]
    identity_values: tuple[str, ...]
    captured_at: str
    billing_window_start: str
    billing_window_end: str
    json_out: Path


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CaptureHoldError(f"invalid_{field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise CaptureHoldError(f"invalid_{field}")
    return parsed.astimezone(UTC)


def _identity_bindings(
    names: tuple[str, ...], values: tuple[str, ...]
) -> list[JsonObject]:
    return [
        {"env": name, "sha256": sha256(value.encode()).hexdigest()}
        for name, value in zip(names, values, strict=True)
    ]


def _require_env(name: str | None, code: str) -> str:
    if not name:
        raise CaptureHoldError(code)
    value = os.environ.get(name)
    if not value:
        raise CaptureHoldError(code)
    return value


def _request_from_args(args: argparse.Namespace) -> CaptureRequest:
    provider = cast("str", args.provider)
    if provider not in SUPPORTED_PROVIDERS:
        raise CaptureHoldError("unsupported_provider")
    capture = _parse_utc(cast("str", args.capture_at), "capture_at")
    start = _parse_utc(cast("str", args.billing_window_start), "billing_window")
    end = _parse_utc(cast("str", args.billing_window_end), "billing_window")
    if not start < capture < end:
        raise CaptureHoldError("invalid_billing_window")
    if provider == "github":
        token = _require_env(args.github_token_env, "github_token_missing")
        names = (cast("str", args.github_repository_id_env),)
    else:
        token = _require_env(args.vercel_token_env, "vercel_token_missing")
        names = (
            cast("str", args.vercel_org_id_env),
            cast("str", args.vercel_project_id_env),
        )
    values = tuple(_require_env(name, "provider_identity_missing") for name in names)
    return CaptureRequest(
        provider=provider,
        token=token,
        identity_envs=names,
        identity_values=values,
        captured_at=capture.isoformat().replace("+00:00", "Z"),
        billing_window_start=start.isoformat().replace("+00:00", "Z"),
        billing_window_end=end.isoformat().replace("+00:00", "Z"),
        json_out=Path(cast("str", args.json_out)),
    )


class _BoundedClient:
    def __init__(self, client: httpx2.Client) -> None:
        self.client = client
        self.aggregate = 0

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str | int] | None = None,
        allow_404: bool = False,
    ) -> tuple[int, bytes, str]:
        try:
            response_context = self.client.stream(
                "GET", url, headers=headers, params=params
            )
            response = response_context.__enter__()
        except httpx2.HTTPError as error:
            raise CaptureHoldError("provider_transport_failed") from error
        try:
            if response.has_redirect_location or 300 <= response.status_code < 400:
                raise CaptureHoldError("provider_redirect_forbidden")
            if response.status_code == 404 and allow_404:
                return response.status_code, b"", ""
            if response.status_code != 200:
                code = (
                    "github_billing_scope_missing"
                    if "github.com" in url and response.status_code in {401, 403, 404}
                    else "provider_http_failed"
                )
                raise CaptureHoldError(code)
            content_type = (
                response.headers.get("content-type", "").split(";", 1)[0].strip()
            )
            if content_type not in {
                "application/json",
                "application/x-ndjson",
                "application/ndjson",
            }:
                raise CaptureHoldError("provider_content_type_invalid")
            chunks: list[bytes] = []
            response_bytes = 0
            for chunk in response.iter_bytes():
                response_bytes += len(chunk)
                self.aggregate += len(chunk)
                if (
                    response_bytes > MAX_RESPONSE_BYTES
                    or self.aggregate > MAX_AGGREGATE_BYTES
                ):
                    raise CaptureHoldError("provider_response_too_large")
                chunks.append(chunk)
            return response.status_code, b"".join(chunks), content_type
        finally:
            response_context.__exit__(None, None, None)


def _json_object(body: bytes, code: str) -> JsonObject:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaptureHoldError(code) from error
    if not isinstance(value, dict):
        raise CaptureHoldError(code)
    return cast("JsonObject", value)


def _uint(value: JsonValue, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CaptureHoldError(code)
    return value


def _number(value: JsonValue, code: str) -> int | float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise CaptureHoldError(code)
    return value


def _github_payloads(
    request: CaptureRequest, client: _BoundedClient
) -> list[JsonObject]:
    repository_id = request.identity_values[0]
    if not repository_id.isdecimal():
        raise CaptureHoldError("github_identity_invalid")
    headers = {
        "Authorization": f"Bearer {request.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    _, body, _ = client.get(
        f"https://api.github.com/repositories/{repository_id}", headers=headers
    )
    repository = _json_object(body, "github_repository_invalid")
    owner = repository.get("owner")
    if (
        repository.get("id") != int(repository_id)
        or repository.get("full_name") != REPOSITORY
        or repository.get("private") is not False
        or not isinstance(owner, dict)
        or owner.get("login") != OWNER
    ):
        raise CaptureHoldError("github_identity_mismatch")
    artifacts: list[JsonObject] = []
    artifact_ids: set[int] = set()
    total_count: int | None = None
    for page in range(1, 1001):
        _, body, _ = client.get(
            f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts",
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        payload = _json_object(body, "github_artifacts_invalid")
        count = _uint(payload.get("total_count"), "github_artifacts_invalid")
        if total_count is None:
            total_count = count
        elif count != total_count:
            raise CaptureHoldError("github_artifacts_changed")
        page_values = payload.get("artifacts")
        if not isinstance(page_values, list):
            raise CaptureHoldError("github_artifacts_invalid")
        for value in page_values:
            if not isinstance(value, dict):
                raise CaptureHoldError("github_artifacts_invalid")
            artifact_id = _uint(value.get("id"), "github_artifacts_invalid")
            if artifact_id in artifact_ids:
                raise CaptureHoldError("github_artifacts_duplicate")
            artifact_ids.add(artifact_id)
            size = _uint(value.get("size_in_bytes"), "github_artifacts_invalid")
            expired = value.get("expired")
            if not isinstance(expired, bool):
                raise CaptureHoldError("github_artifacts_invalid")
            artifacts.append(
                {
                    "id": artifact_id,
                    "size_in_bytes": size,
                    "created_at": value.get("created_at"),
                    "expires_at": value.get("expires_at"),
                    "expired": expired,
                }
            )
        if len(page_values) < 100:
            break
    else:
        raise CaptureHoldError("github_artifacts_pagination_incomplete")
    if total_count != len(artifact_ids):
        raise CaptureHoldError("github_artifacts_pagination_incomplete")
    _, body, _ = client.get(
        f"https://api.github.com/repos/{REPOSITORY}/actions/cache/usage",
        headers=headers,
    )
    cache = _json_object(body, "github_cache_invalid")
    cache_projection: JsonObject = {
        "active_caches_size_in_bytes": _uint(
            cache.get("active_caches_size_in_bytes"), "github_cache_invalid"
        ),
        "active_caches_count": _uint(
            cache.get("active_caches_count"), "github_cache_invalid"
        ),
    }
    capture = _parse_utc(request.captured_at, "capture_at")
    params = {
        "year": capture.year,
        "month": capture.month,
        "repository": REPOSITORY,
    }
    _, body, _ = client.get(
        f"https://api.github.com/users/{OWNER}/settings/billing/usage/summary",
        headers=headers,
        params=params,
    )
    billing = _json_object(body, "github_billing_invalid")
    billing_keys = set(billing)
    if (
        billing_keys
        not in (
            {"timePeriod", "user", "usageItems"},
            {"timePeriod", "user", "repository", "usageItems"},
        )
        or billing.get("user") != OWNER
        or (
            "repository" in billing
            and billing.get("repository") != REPOSITORY
        )
    ):
        raise CaptureHoldError("github_billing_invalid")
    period = billing.get("timePeriod")
    if (
        not isinstance(period, dict)
        or set(period) != {"year", "month"}
        or period.get("year") != capture.year
        or period.get("month") != capture.month
    ):
        raise CaptureHoldError("github_billing_window_mismatch")
    items = billing.get("usageItems")
    if not isinstance(items, list):
        raise CaptureHoldError("github_billing_invalid")
    billing_projection: list[JsonObject] = []
    present_dimensions: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise CaptureHoldError("github_billing_invalid")
        keys = frozenset(item)
        if keys not in {GITHUB_ITEM_FIELDS, GITHUB_ITEM_FIELDS | {"model"}}:
            raise CaptureHoldError("github_billing_invalid")
        if "model" in item and not isinstance(item.get("model"), str):
            raise CaptureHoldError("github_billing_invalid")
        for field in GITHUB_ITEM_FIELDS - {"product", "sku", "unitType"}:
            _ = _number(item.get(field), "github_billing_invalid")
        if not all(
            isinstance(item.get(field), str) for field in ("product", "sku", "unitType")
        ):
            raise CaptureHoldError("github_billing_invalid")
        usage_tuple = (
            cast("str", item["product"]),
            cast("str", item["sku"]),
            cast("str", item["unitType"]),
        )
        dimension = GITHUB_USAGE_TUPLES.get(usage_tuple)
        if dimension is None:
            raise CaptureHoldError("github_billing_scope_missing")
        present_dimensions.add(dimension)
        billing_projection.append(
            cast(
                "JsonObject",
                {field: item[field] for field in GITHUB_ITEM_FIELDS},
            )
        )
    if not present_dimensions.issubset(GITHUB_REQUIRED_BILLING_DIMENSIONS):
        raise CaptureHoldError("github_billing_scope_missing")
    return [
        {
            "kind": "repository",
            "value": {
                "id": repository["id"],
                "full_name": REPOSITORY,
                "private": False,
            },
        },
        {"kind": "artifacts", "value": artifacts},
        {"kind": "cache-usage", "value": cache_projection},
        {
            "kind": "billing-summary",
            "request_scope": {
                "year": capture.year,
                "month": capture.month,
                "repository": REPOSITORY,
            },
            "time_period": cast("JsonValue", period),
            "value": billing_projection,
        },
    ]


def _focus_records(
    body: bytes,
    *,
    request: CaptureRequest,
    expected_name: str,
) -> list[JsonObject]:
    records: list[JsonObject] = []
    window_start = _parse_utc(request.billing_window_start, "billing_window")
    window_end = _parse_utc(request.billing_window_end, "billing_window")
    present_dimensions: set[str] = set()
    try:
        decoded = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CaptureHoldError("vercel_focus_invalid") from error
    if decoded and not decoded.endswith("\n"):
        raise CaptureHoldError("vercel_focus_truncated")
    lines = decoded.splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise CaptureHoldError("vercel_focus_invalid") from error
        if not isinstance(value, dict) or frozenset(value) != FOCUS_FIELDS:
            raise CaptureHoldError("vercel_focus_invalid")
        string_fields = FOCUS_FIELDS - {"ConsumedQuantity", "BilledCost"}
        if not all(
            isinstance(value.get(field), str) and bool(cast("str", value[field]))
            for field in string_fields
        ):
            raise CaptureHoldError("vercel_focus_invalid")
        if (
            value.get("BillingAccountId") != request.identity_values[0]
            or value.get("ResourceId") != request.identity_values[1]
            or value.get("ResourceName") != expected_name
        ):
            raise CaptureHoldError("vercel_focus_identity_mismatch")
        for field in ("ConsumedQuantity", "BilledCost"):
            _ = _number(value.get(field), "vercel_focus_invalid")
        charge_start = _parse_utc(
            cast("str", value["ChargePeriodStart"]), "focus_charge_period"
        )
        charge_end = _parse_utc(
            cast("str", value["ChargePeriodEnd"]), "focus_charge_period"
        )
        if not (window_start <= charge_start < charge_end <= window_end):
            raise CaptureHoldError("vercel_focus_window_mismatch")
        if value.get("BillingCurrency") != "USD":
            raise CaptureHoldError("vercel_focus_invalid")
        focus_tuple = (
            cast("str", value["ServiceName"]),
            cast("str", value["SkuId"]),
            cast("str", value["ConsumedUnit"]),
        )
        dimension = VERCEL_FOCUS_TUPLES[request.provider].get(focus_tuple)
        if dimension is None:
            raise CaptureHoldError("vercel_focus_tuple_unknown")
        present_dimensions.add(dimension)
        records.append(cast("JsonObject", {key: value[key] for key in FOCUS_FIELDS}))
    if records and present_dimensions != set(
        VERCEL_FOCUS_TUPLES[request.provider].values()
    ):
        raise CaptureHoldError("vercel_focus_dimension_missing")
    return records


def _vercel_payloads(
    request: CaptureRequest, client: _BoundedClient
) -> list[JsonObject]:
    org_id, project_id = request.identity_values
    headers = {
        "Authorization": f"Bearer {request.token}",
        "Accept": "application/x-ndjson, application/json",
        "Accept-Encoding": "gzip",
    }
    _, body, _ = client.get(
        f"https://api.vercel.com/v2/teams/{org_id}", headers=headers
    )
    team = _json_object(body, "vercel_team_invalid")
    billing = team.get("billing")
    if (
        team.get("id") != org_id
        or team.get("slug") != TEAM_SLUG
        or not isinstance(team.get("name"), str)
        or not cast("str", team["name"])
        or not isinstance(billing, dict)
        or billing.get("plan") != "hobby"
    ):
        raise CaptureHoldError("vercel_team_mismatch")
    _, body, _ = client.get(
        f"https://api.vercel.com/v9/projects/{project_id}",
        headers=headers,
        params={"teamId": org_id},
    )
    project = _json_object(body, "vercel_project_invalid")
    expected_name = (
        "prediction-monitor-api"
        if request.provider == "vercel-api"
        else "prediction-monitor-web"
    )
    if (
        project.get("id") != project_id
        or project.get("name") != expected_name
        or project.get("accountId") != org_id
    ):
        raise CaptureHoldError("vercel_project_mismatch")
    params = {
        "teamId": org_id,
        "from": request.billing_window_start,
        "to": request.billing_window_end,
    }
    status, focus_body, focus_content_type = client.get(
        "https://api.vercel.com/v1/billing/charges",
        headers=headers,
        params=params,
        allow_404=True,
    )
    if status != 404 and focus_content_type not in {
        "application/x-ndjson",
        "application/ndjson",
    }:
        raise CaptureHoldError("vercel_focus_content_type_invalid")
    focus = (
        []
        if status == 404
        else _focus_records(
            focus_body,
            request=request,
            expected_name=expected_name,
        )
    )
    usage_status = "complete" if focus else "dashboard_required"
    return [
        {
            "kind": "team",
            "value": {
                "id": org_id,
                "slug": TEAM_SLUG,
                "name": team.get("name"),
                "billing": {"plan": "hobby"},
            },
        },
        {
            "kind": "project",
            "value": {"id": project_id, "name": expected_name, "accountId": org_id},
        },
        {
            "kind": "focus-billing",
            "status": usage_status,
            "request_scope": params,
            "value": focus,
        },
    ]


def acquire(
    request: CaptureRequest, *, transport: httpx2.BaseTransport | None = None
) -> JsonObject:
    """Acquire one schema-closed private provider payload document."""
    if request.provider not in SUPPORTED_PROVIDERS:
        raise CaptureHoldError("unsupported_provider")
    client_transport = transport or httpx2.HTTPTransport(retries=0, limits=LIMITS)
    with httpx2.Client(
        transport=client_transport,
        timeout=TIMEOUT,
        follow_redirects=False,
    ) as raw_client:
        client = _BoundedClient(raw_client)
        payloads = (
            _github_payloads(request, client)
            if request.provider == "github"
            else _vercel_payloads(request, client)
        )
    return {
        "schema": SCHEMA,
        "provider": request.provider,
        "captured_at": request.captured_at,
        "billing_window_start": request.billing_window_start,
        "billing_window_end": request.billing_window_end,
        "identity_bindings": cast(
            "JsonValue",
            _identity_bindings(request.identity_envs, request.identity_values),
        ),
        "official_payloads": cast("JsonValue", payloads),
    }


def _directory_fingerprint(status: os.stat_result) -> tuple[int, int]:
    return status.st_dev, status.st_ino


def _require_private_parent(path: Path) -> tuple[os.stat_result, Path]:
    parent = path.parent
    if path.exists() or path.is_symlink() or parent.is_symlink():
        raise CaptureHoldError("private_output_exists_or_aliases")
    try:
        status = parent.stat(follow_symlinks=False)
        resolved = parent.resolve(strict=True)
    except OSError as error:
        raise CaptureHoldError("private_parent_unavailable") from error
    absolute_parent = Path(os.path.abspath(parent))
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute_parent)):
        raise CaptureHoldError("private_parent_alias_detected")
    if not stat.S_ISDIR(status.st_mode):
        raise CaptureHoldError("private_parent_invalid")
    if os.name == "nt":
        if not _windows_acl_owner_only(parent):
            raise CaptureHoldError("private_parent_acl_invalid")
    else:
        getuid = getattr(os, "getuid", None)
        if (
            getuid is None
            or status.st_uid != getuid()
            or bool(status.st_mode & (stat.S_IRWXG | stat.S_IRWXO))
        ):
            raise CaptureHoldError("private_parent_acl_invalid")
    return status, resolved


def _harden_windows(path: Path) -> None:
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    powershell = (
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    script = (
        "$target=$env:PROVIDER_CAPTURE_ACL_TARGET;"
        "$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name;"
        "$acl=New-Object System.Security.AccessControl.FileSecurity;"
        "$acl.SetAccessRuleProtection($true,$false);"
        "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule("
        "$identity,'FullControl','Allow');"
        "$acl.AddAccessRule($rule);"
        "[System.IO.File]::SetAccessControl($target,$acl)"
    )
    try:
        _ = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=True,
            capture_output=True,
            env={
                **{
                    name: value
                    for name in (
                        "SYSTEMROOT",
                        "WINDIR",
                        "COMSPEC",
                        "PATH",
                        "PATHEXT",
                    )
                    if (value := os.environ.get(name))
                },
                "PROVIDER_CAPTURE_ACL_TARGET": str(path),
            },
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureHoldError("private_output_acl_failed") from error
    if not _windows_acl_owner_only(path):
        raise CaptureHoldError("private_output_acl_failed")


def write_private(path: Path, document: JsonObject) -> None:
    """Exclusively write one owner-private canonical JSON document."""
    parent_status, resolved_parent = _require_private_parent(path)
    payload = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    parent_descriptor: int | None = None
    created_fingerprint: tuple[int, int] | None = None
    with hold_private_directory(resolved_parent):
        try:
            locked_parent = resolved_parent.stat(follow_symlinks=False)
            if (
                _directory_fingerprint(locked_parent)
                != _directory_fingerprint(parent_status)
                or path.parent.resolve(strict=True) != resolved_parent
            ):
                raise CaptureHoldError("private_parent_changed")
            if os.name == "nt":
                descriptor = os.open(path, flags | nofollow, 0o600)
            else:
                directory_flags = (
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
                )
                parent_descriptor = os.open(resolved_parent, directory_flags)
                opened_parent = os.fstat(parent_descriptor)
                if _directory_fingerprint(opened_parent) != _directory_fingerprint(
                    parent_status
                ):
                    raise CaptureHoldError("private_parent_changed")
                descriptor = os.open(
                    path.name,
                    flags | nofollow,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            created_status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(created_status.st_mode)
                or created_status.st_nlink != 1
            ):
                raise CaptureHoldError("private_output_alias_detected")
            created_fingerprint = _directory_fingerprint(created_status)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            current_parent = resolved_parent.stat(follow_symlinks=False)
            current_output = path.stat(follow_symlinks=False)
            if (
                _directory_fingerprint(current_parent)
                != _directory_fingerprint(parent_status)
                or _directory_fingerprint(current_output) != created_fingerprint
                or not stat.S_ISREG(current_output.st_mode)
                or current_output.st_nlink != 1
                or path.parent.resolve(strict=True) != resolved_parent
            ):
                raise CaptureHoldError("private_output_alias_detected")
            if os.name == "nt":
                _harden_windows(path)
            else:
                path.chmod(0o600)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            try:
                current = path.stat(follow_symlinks=False)
                if (
                    created_fingerprint is not None
                    and _directory_fingerprint(current) == created_fingerprint
                    and path.parent.resolve(strict=True) == resolved_parent
                ):
                    path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            if parent_descriptor is not None:
                os.close(parent_descriptor)


def build_parser() -> argparse.ArgumentParser:
    """Build the sole capture subcommand contract."""
    parser = argparse.ArgumentParser(prog="provider_capture_acquire.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--provider", required=True)
    capture.add_argument("--github-token-env")
    capture.add_argument("--github-repository-id-env")
    capture.add_argument("--vercel-token-env")
    capture.add_argument("--vercel-org-id-env")
    capture.add_argument("--vercel-project-id-env")
    capture.add_argument("--capture-at", required=True)
    capture.add_argument("--billing-window-start", required=True)
    capture.add_argument("--billing-window-end", required=True)
    capture.add_argument("--json-out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one private acquisition without successful terminal output."""
    try:
        args = build_parser().parse_args(argv)
        request = _request_from_args(args)
        write_private(request.json_out, acquire(request))
    except CaptureHoldError as error:
        print(f"provider-capture HOLD: {error.code}", file=sys.stderr)
        return 42
    except Exception:
        print("provider-capture HOLD: unexpected_failure", file=sys.stderr)
        return 42
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
