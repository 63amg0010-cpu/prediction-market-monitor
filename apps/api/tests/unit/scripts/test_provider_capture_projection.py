from __future__ import annotations

# pyright: reportAny=false, reportInvalidTypeForm=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# ruff: noqa: E501, FLY002, S603, S607
import base64
import importlib
import json
import os
import struct
import subprocess
import sys
import zlib
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
free_tier_projection = importlib.import_module("apps.api.scripts.free_tier_projection")
acquire = importlib.import_module("apps.api.scripts.provider_capture_acquire")
MODULE = ROOT / "apps" / "api" / "scripts" / "provider_capture_projection.mjs"
FIXTURES = ROOT / "apps" / "api" / "tests" / "fixtures" / "free-tier"


def _node(source: str, payload: object, env: dict[str, str] | None = None) -> object:
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", source],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _module_url() -> str:
    return MODULE.resolve().as_uri()


def _png_bytes(red: int) -> bytes:
    width = 640
    height = 360
    row = bytes((0,)) + bytes((red, 37, 91, 255)) * width
    pixels = row * height

    def chunk(kind: bytes, value: bytes) -> bytes:
        body = kind + value
        return struct.pack(">I", len(value)) + body + struct.pack(">I", zlib.crc32(body))

    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(pixels)),
            chunk(b"IEND", b""),
        )
    )


def _current_usage(verified: dict[str, object]) -> dict[str, int]:
    captured = datetime.fromisoformat(cast("str", verified["captured_at"]))
    result: dict[str, int] = {}
    for raw in cast("list[dict[str, object]]", verified["dimensions"]):
        start = datetime.fromisoformat(cast("str", raw["window_start"]))
        end = datetime.fromisoformat(cast("str", raw["window_end"]))
        if start <= captured < end:
            result[cast("str", raw["name"])] = cast("int", raw["observed_usage"])
    return result


def _github_official(
    verified: dict[str, object], identity: str = "123"
) -> dict[str, object]:
    usage = _current_usage(verified)
    captured = datetime.fromisoformat(cast("str", verified["captured_at"]))
    items = []
    for product, sku, unit, dimension in (
        ("Actions", "actions_linux", "minutes", "github_actions_minutes"),
        ("Actions", "actions_storage", "gigabyte-hours", "github_artifact_gb_hours"),
        ("Packages", "packages_storage", "gigabyte-hours", "github_packages_gb_hours"),
    ):
        value = usage[dimension]
        items.append(
            {
                "product": product,
                "sku": sku,
                "unitType": unit,
                "pricePerUnit": 0,
                "grossQuantity": value,
                "grossAmount": 0,
                "discountQuantity": 0,
                "discountAmount": 0,
                "netQuantity": value,
                "netAmount": 0,
            }
        )
    return {
        "schema": acquire.SCHEMA,
        "provider": "github",
        "captured_at": verified["captured_at"],
        "billing_window_start": "2026-07-01T00:00:00Z",
        "billing_window_end": "2026-08-01T00:00:00Z",
        "identity_bindings": [
            {
                "env": "GITHUB_REPOSITORY_ID",
                "sha256": sha256(identity.encode()).hexdigest(),
            }
        ],
        "official_payloads": [
            {
                "kind": "repository",
                "value": {
                    "id": int(identity),
                    "full_name": acquire.REPOSITORY,
                    "private": False,
                },
            },
            {"kind": "artifacts", "value": []},
            {
                "kind": "cache-usage",
                "value": {
                    "active_caches_size_in_bytes": usage["github_cache_bytes"],
                    "active_caches_count": 0,
                },
            },
            {
                "kind": "billing-summary",
                "request_scope": {
                    "year": captured.year,
                    "month": captured.month,
                    "repository": acquire.REPOSITORY,
                },
                "time_period": {"year": captured.year, "month": captured.month},
                "value": items,
            },
        ],
    }


def _github_projection_input() -> dict[str, object]:
    verified = cast(
        "dict[str, object]",
        json.loads((FIXTURES / "github-verified.json").read_text(encoding="utf-8")),
    )
    captured = datetime.fromisoformat(cast("str", verified["captured_at"]))
    current: dict[str, dict[str, object]] = {}
    for value in cast("list[dict[str, object]]", verified["dimensions"]):
        start = datetime.fromisoformat(cast("str", value["window_start"]))
        end = datetime.fromisoformat(cast("str", value["window_end"]))
        if start <= captured < end:
            current[cast("str", value["name"])] = value
    snapshot = "\n".join(
        (
            'document "Billing usage"',
            "URL: https://github.com/settings/billing/usage",
            "Account: 63amg0010-cpu",
            f"Repository: {acquire.REPOSITORY}",
            "Plan: Public repository (public-standard)",
            "Paid usage disabled",
            "Billing period: 2026-07-01T00:00:00Z through 2026-08-01T00:00:00Z",
            f"Actions minutes: {current['github_actions_minutes']['observed_usage']} / {current['github_actions_minutes']['quota']} minutes",
            f"Artifact storage: {current['github_artifact_gb_hours']['observed_usage']} / {current['github_artifact_gb_hours']['quota']} gigabyte-hours",
            f"Packages storage: {current['github_packages_gb_hours']['observed_usage']} / {current['github_packages_gb_hours']['quota']} gigabyte-hours",
            f"Actions cache: {current['github_cache_bytes']['observed_usage']} / {current['github_cache_bytes']['quota']} bytes",
        )
    )
    first_dimension = cast("list[dict[str, object]]", verified["dimensions"])[0]
    manifest = cast("dict[str, object]", first_dimension["projection_operands"])
    manifest_traffic = cast("dict[str, object]", manifest["traffic"])
    return {
        "provider": "github",
        "publicProject": acquire.REPOSITORY,
        "officialPayloads": _github_official(verified),
        "dashboardSnapshot": snapshot,
        "capturedAt": verified["captured_at"],
        "billingWindowStart": "2026-07-01T00:00:00Z",
        "billingWindowEnd": "2026-08-01T00:00:00Z",
        "trailing30dPageRequests": manifest_traffic["trailing_30d_page_requests"],
        "workloadManifest": manifest,
    }


def test_javascript_added_usage_matches_python_byte_for_byte() -> None:
    verified = json.loads(
        (FIXTURES / "github-verified.json").read_text(encoding="utf-8")
    )
    dimension = verified["dimensions"][0]
    expected = free_tier_projection.derive_added_usage_raw(
        cast("dict[str, object]", dimension),
        verified["captured_at"],
    )
    source = f"""
      import {{ deriveAddedUsageRaw }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      process.stdout.write(JSON.stringify(deriveAddedUsageRaw(value.dimension, value.capturedAt)));
    """

    actual = _node(
        source,
        {"dimension": dimension, "capturedAt": verified["captured_at"]},
    )

    assert actual == expected == dimension["added_usage_raw"]


def test_project_provider_capture_is_schema_closed_and_threshold_bound() -> None:
    verified = json.loads(
        (FIXTURES / "github-verified.json").read_text(encoding="utf-8")
    )
    payload = _github_projection_input()
    source = f"""
      import {{ projectProviderCapture }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      process.stdout.write(JSON.stringify(projectProviderCapture(value)));
    """

    result = cast(
        "dict[str, object]",
        _node(
            source,
            payload,
            env={"GITHUB_REPOSITORY_ID": "123"},
        ),
    )
    observation = cast("dict[str, object]", result["observation"])
    response = cast("dict[str, object]", result["response"])
    projected = cast("list[dict[str, object]]", observation["dimensions"])

    assert observation["schema"] == "free-tier.provider-observation.v1"
    assert response["schema"] == "free-tier.provider-private-response.v1"
    assert (
        response["observation_sha256"]
        == sha256(
            json.dumps(
                observation,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert (
        projected[0]["added_usage_raw"] == verified["dimensions"][0]["added_usage_raw"]
    )


def test_github_omitted_zero_sku_requires_dashboard_zero() -> None:
    payload = _github_projection_input()
    official = cast("dict[str, object]", payload["officialPayloads"])
    payloads = cast("list[dict[str, object]]", official["official_payloads"])
    billing = payloads[3]
    items = cast("list[dict[str, object]]", billing["value"])
    billing["value"] = [item for item in items if item["product"] != "Packages"]
    snapshot = cast("str", payload["dashboardSnapshot"])
    payload["dashboardSnapshot"] = snapshot.replace(
        "Packages storage: 10 /",
        "Packages storage: 0 /",
        1,
    )
    source = f"""
      import {{ projectProviderCapture }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      process.stdout.write(JSON.stringify(projectProviderCapture(value).observation));
    """
    observation = cast(
        "dict[str, object]",
        _node(source, payload, env={"GITHUB_REPOSITORY_ID": "123"}),
    )
    dimensions = cast("list[dict[str, object]]", observation["dimensions"])
    current = [
        value
        for value in dimensions
        if value["name"] == "github_packages_gb_hours"
        and value["observed_usage"] == 0
    ]
    assert current

    payload["dashboardSnapshot"] = snapshot
    rejecting_source = f"""
      import {{ projectProviderCapture }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      try {{ projectProviderCapture(value); process.stdout.write(JSON.stringify('unexpected_success')); }}
      catch (error) {{ process.stdout.write(JSON.stringify(error.code)); }}
    """
    assert (
        _node(
            rejecting_source,
            payload,
            env={"GITHUB_REPOSITORY_ID": "123"},
        )
        == "official_dashboard_counter_mismatch"
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("foreign_official", "github_repository_identity_mismatch"),
        ("counter", "official_dashboard_counter_mismatch"),
        ("origin", "dashboard_identity_missing"),
        ("account", "dashboard_identity_missing"),
        ("synthetic_marker", "dashboard_snapshot_invalid"),
    ],
)
def test_projection_rejects_unrelated_official_dashboard_or_account(
    case: str,
    expected: str,
) -> None:
    payload = _github_projection_input()
    if case == "foreign_official":
        official = cast("dict[str, object]", payload["officialPayloads"])
        official_payloads = cast(
            "list[dict[str, object]]", official["official_payloads"]
        )
        repository = cast("dict[str, object]", official_payloads[0]["value"])
        repository["id"] = 999
    else:
        snapshot = cast("str", payload["dashboardSnapshot"])
        if case == "counter":
            snapshot = snapshot.replace("Actions minutes: 10 /", "Actions minutes: 11 /", 1)
        elif case == "origin":
            snapshot = snapshot.replace(
                "https://github.com/settings/billing/usage",
                "https://example.com/forged",
                1,
            )
        elif case == "account":
            snapshot = snapshot.replace("63amg0010-cpu", "foreign-account")
        else:
            snapshot = "PROVIDER_CAPTURE_JSON_BEGIN\n" + snapshot
        payload["dashboardSnapshot"] = snapshot
    source = f"""
      import {{ projectProviderCapture }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      try {{ projectProviderCapture(value); process.stdout.write(JSON.stringify('unexpected_success')); }}
      catch (error) {{ process.stdout.write(JSON.stringify(error.code)); }}
    """
    assert _node(source, payload, env={"GITHUB_REPOSITORY_ID": "123"}) == expected


def _owner_only_directory(path: Path) -> None:
    path.mkdir(mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
        return
    script = ";".join(
        (
            "$target=$env:PROVIDER_CAPTURE_ACL_TARGET",
            "$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
            "$acl=New-Object System.Security.AccessControl.DirectorySecurity",
            "$acl.SetAccessRuleProtection($true,$false)",
            "$inherit=[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'",
            "$prop=[System.Security.AccessControl.PropagationFlags]::None",
            "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule($identity,'FullControl',$inherit,$prop,'Allow')",
            "$acl.AddAccessRule($rule)",
            "[System.IO.Directory]::SetAccessControl($target,$acl)",
        )
    )
    _ = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        env={**os.environ, "PROVIDER_CAPTURE_ACL_TARGET": str(path)},
        text=True,
        timeout=10,
    )


def test_private_acquisition_output_is_consumed_by_node_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "private"
    _owner_only_directory(parent)
    output = parent / "official-payloads.json"
    identity = "123"
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", identity)
    verified = cast(
        "dict[str, object]",
        json.loads((FIXTURES / "github-verified.json").read_text(encoding="utf-8")),
    )
    document = cast("acquire.JsonObject", _github_official(verified, identity))
    if os.name == "nt":
        monkeypatch.setattr(acquire, "_windows_acl_owner_only", lambda _path: True)
    acquire.write_private(output, document)
    source = f"""
      import {{ loadPrivateOfficialPayloads }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      const result = await loadPrivateOfficialPayloads(value.path, 'github', ['GITHUB_REPOSITORY_ID']);
      process.stdout.write(JSON.stringify(result));
    """

    loaded = _node(
        source,
        {"path": str(output)},
        env={
            "GITHUB_REPOSITORY_ID": identity,
            "PROVIDER_CAPTURE_PYTHON": str(tmp_path / "attacker-controlled.exe"),
        },
    )

    assert loaded == document
    assert (
        output.read_bytes()
        == json.dumps(
            loaded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def test_node_projection_transaction_writes_only_expected_private_files(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-captures"
    provider_root = private_root / "github"
    _owner_only_directory(private_root)
    _owner_only_directory(provider_root)
    payload = _github_projection_input()
    screenshot = base64.b64encode(_png_bytes(120)).decode()
    payload.update(
        {
            "privateRoot": str(private_root),
            "screenshotBytes": {"data": screenshot},
            "liveScreenshot": screenshot,
        }
    )
    source = f"""
      import {{ projectAndPersistProviderCapture }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      const liveScreenshot = Buffer.from(value.liveScreenshot, 'base64');
      delete value.liveScreenshot;
      value.tab = {{
        playwright: {{ domSnapshot: async () => value.dashboardSnapshot }},
        screenshot: async (options) => {{
          if (options.fullPage !== false) throw new Error('screenshot_options_invalid');
          return liveScreenshot;
        }}
      }};
      const result = await projectAndPersistProviderCapture(value);
      process.stdout.write(JSON.stringify(result));
    """

    result = cast(
        "dict[str, object]",
        _node(source, payload, env={"GITHUB_REPOSITORY_ID": "123"}),
    )

    assert result["provider"] == "github"
    assert {path.name for path in provider_root.iterdir()} == {
        "observation.json",
        "response.json",
        "screenshot.png",
    }
    assert all(path.stat().st_size > 0 for path in provider_root.iterdir())


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("placeholder", "screenshot_invalid"),
        ("public_document_substitution", "screenshot_provenance_invalid"),
        ("public_document_tab", "dashboard_snapshot_provenance_invalid"),
    ],
)
def test_projection_rejects_unbound_screenshot_before_output(
    tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    private_root = tmp_path / "private-captures"
    provider_root = private_root / "github"
    _owner_only_directory(private_root)
    _owner_only_directory(provider_root)
    account_screenshot = base64.b64encode(_png_bytes(120)).decode()
    public_screenshot = base64.b64encode(_png_bytes(10)).decode()
    supplied = "iVBORw0KGgo=" if case == "placeholder" else public_screenshot
    payload = _github_projection_input()
    payload.update(
        {
            "privateRoot": str(private_root),
            "screenshotBytes": {"data": supplied},
            "liveScreenshot": (
                public_screenshot
                if case == "public_document_tab"
                else account_screenshot
            ),
            "liveSnapshot": (
                'document "Public billing documentation"'
                if case == "public_document_tab"
                else payload["dashboardSnapshot"]
            ),
        }
    )
    source = f"""
      import {{ projectAndPersistProviderCapture }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      const liveScreenshot = Buffer.from(value.liveScreenshot, 'base64');
      const liveSnapshot = value.liveSnapshot;
      delete value.liveScreenshot;
      delete value.liveSnapshot;
      value.tab = {{
        playwright: {{ domSnapshot: async () => liveSnapshot }},
        screenshot: async () => liveScreenshot
      }};
      try {{
        await projectAndPersistProviderCapture(value);
        process.stdout.write(JSON.stringify('unexpected_success'));
      }} catch (error) {{
        process.stdout.write(JSON.stringify(error.code));
      }}
    """

    assert _node(source, payload, env={"GITHUB_REPOSITORY_ID": "123"}) == expected
    assert list(provider_root.iterdir()) == []


def test_private_loader_rejects_hardlink_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "private"
    _owner_only_directory(parent)
    output = parent / "official-payloads.json"
    identity = "123"
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", identity)
    verified = cast(
        "dict[str, object]",
        json.loads((FIXTURES / "github-verified.json").read_text(encoding="utf-8")),
    )
    if os.name == "nt":
        monkeypatch.setattr(acquire, "_windows_acl_owner_only", lambda _path: True)
    acquire.write_private(
        output,
        cast("acquire.JsonObject", _github_official(verified, identity)),
    )
    os.link(output, parent / "alias.json")
    source = f"""
      import {{ loadPrivateOfficialPayloads }} from {json.dumps(_module_url())};
      let input = '';
      for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input);
      try {{ await loadPrivateOfficialPayloads(value.path, 'github', ['GITHUB_REPOSITORY_ID']); process.stdout.write(JSON.stringify('unexpected_success')); }}
      catch (error) {{ process.stdout.write(JSON.stringify(error.code)); }}
    """
    assert (
        _node(
            source,
            {"path": str(output)},
            env={"GITHUB_REPOSITORY_ID": identity},
        )
        == "private_file_invalid"
    )


def test_projection_source_forbids_supabase_management_credentials() -> None:
    acquisition_source = (
        ROOT / "apps" / "api" / "scripts" / "provider_capture_acquire.py"
    ).read_text(encoding="utf-8")
    projection_source = MODULE.read_text(encoding="utf-8")

    assert "SUPABASE_ACCESS_TOKEN" not in acquisition_source
    assert "SUPABASE_ACCESS_TOKEN" not in projection_source
    assert "api.supabase.com" not in acquisition_source
