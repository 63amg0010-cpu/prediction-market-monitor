from __future__ import annotations

# pyright: reportAny=false, reportInvalidTypeForm=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false
# ruff: noqa: S106
import importlib
import json
import os
import sys
from pathlib import Path
from typing import cast

import httpx2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
acquire = importlib.import_module("apps.api.scripts.provider_capture_acquire")
private_lock = importlib.import_module(
    "apps.api.scripts.private_windows_directory_lock"
)


def _request(tmp_path: Path, provider: str) -> acquire.CaptureRequest:
    if provider == "github":
        names = ("GITHUB_REPOSITORY_ID",)
        values = ("123",)
    else:
        names = ("VERCEL_ORG_ID", "VERCEL_API_PROJECT_ID")
        values = ("team_private", "project_private")
    return acquire.CaptureRequest(
        provider=provider,
        token="private-token-sentinel",
        identity_envs=names,
        identity_values=values,
        captured_at="2026-08-02T12:00:00Z",
        billing_window_start="2026-08-01T00:00:00Z",
        billing_window_end="2026-09-01T00:00:00Z",
        json_out=tmp_path / "official-payloads.json",
    )


def _json_response(payload: object) -> httpx2.Response:
    return httpx2.Response(
        200,
        headers={"content-type": "application/json"},
        json=payload,
    )


def _github_usage_items(value: int = 0) -> list[dict[str, object]]:
    return [
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
        for product, sku, unit in (
            ("Actions", "actions_linux", "minutes"),
            ("Actions", "actions_storage", "gigabyte-hours"),
            ("Packages", "packages_storage", "gigabyte-hours"),
        )
    ]


def _github_transport(
    items: list[dict[str, object]], *, raw_json: bool = False
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/repositories/123":
            return _json_response(
                {
                    "id": 123,
                    "full_name": acquire.REPOSITORY,
                    "private": False,
                    "owner": {"login": acquire.OWNER},
                }
            )
        if path.endswith("/actions/artifacts"):
            return _json_response({"total_count": 0, "artifacts": []})
        if path.endswith("/actions/cache/usage"):
            return _json_response(
                {"active_caches_size_in_bytes": 0, "active_caches_count": 0}
            )
        billing = {
            "timePeriod": {"year": 2026, "month": 8},
            "user": acquire.OWNER,
            "usageItems": items,
        }
        if raw_json:
            return httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps(billing, allow_nan=True).encode(),
            )
        return _json_response(billing)

    return httpx2.MockTransport(handler)


def test_github_acquisition_uses_pinned_routes_headers_and_schema(
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        path = request.url.path
        if path == "/repositories/123":
            return _json_response(
                {
                    "id": 123,
                    "full_name": acquire.REPOSITORY,
                    "private": False,
                    "owner": {"login": acquire.OWNER},
                }
            )
        if path.endswith("/actions/artifacts"):
            return _json_response({"total_count": 0, "artifacts": []})
        if path.endswith("/actions/cache/usage"):
            return _json_response(
                {"active_caches_size_in_bytes": 0, "active_caches_count": 0}
            )
        if path.endswith("/settings/billing/usage/summary"):
            return _json_response(
                {
                    "timePeriod": {"year": 2026, "month": 8},
                    "user": acquire.OWNER,
                    "usageItems": _github_usage_items(),
                }
            )
        return httpx2.Response(500)

    document = acquire.acquire(
        _request(tmp_path, "github"),
        transport=httpx2.MockTransport(handler),
    )

    assert document["schema"] == acquire.SCHEMA
    assert [request.url.path for request in requests] == [
        "/repositories/123",
        f"/repos/{acquire.REPOSITORY}/actions/artifacts",
        f"/repos/{acquire.REPOSITORY}/actions/cache/usage",
        f"/users/{acquire.OWNER}/settings/billing/usage/summary",
    ]
    assert dict(requests[1].url.params) == {"per_page": "100", "page": "1"}
    assert requests[3].url.params["repository"] == acquire.REPOSITORY
    assert all(
        request.headers["x-github-api-version"] == "2026-03-10" for request in requests
    )
    rendered = json.dumps(document)
    assert "private-token-sentinel" not in rendered


def test_github_summary_rejects_report_only_date_and_repository_fields(
    tmp_path: Path,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/repositories/123":
            return _json_response(
                {
                    "id": 123,
                    "full_name": acquire.REPOSITORY,
                    "private": False,
                    "owner": {"login": acquire.OWNER},
                }
            )
        if path.endswith("/actions/artifacts"):
            return _json_response({"total_count": 0, "artifacts": []})
        if path.endswith("/actions/cache/usage"):
            return _json_response(
                {"active_caches_size_in_bytes": 0, "active_caches_count": 0}
            )
        return _json_response(
            {
                "timePeriod": {"year": 2026, "month": 8},
                "user": acquire.OWNER,
                "usageItems": [
                    {
                        "date": "2026-08-01",
                        "repositoryName": acquire.REPOSITORY,
                        "product": "Actions",
                        "sku": "actions_linux",
                        "unitType": "minutes",
                        "pricePerUnit": 0.008,
                        "grossQuantity": 0,
                        "grossAmount": 0,
                        "discountQuantity": 0,
                        "discountAmount": 0,
                        "netQuantity": 0,
                        "netAmount": 0,
                    }
                ],
            }
        )

    with pytest.raises(acquire.CaptureHoldError, match="github_billing_invalid"):
        _ = acquire.acquire(
            _request(tmp_path, "github"),
            transport=httpx2.MockTransport(handler),
        )


@pytest.mark.parametrize("case", ["unknown", "missing", "nonfinite"])
def test_github_summary_rejects_unmapped_incomplete_or_nonfinite_usage(
    tmp_path: Path,
    case: str,
) -> None:
    items = _github_usage_items()
    if case == "unknown":
        items[0]["sku"] = "unreviewed_sku"
    elif case == "missing":
        _ = items.pop()
    else:
        items[0]["netQuantity"] = float("inf")
    with pytest.raises(acquire.CaptureHoldError):
        _ = acquire.acquire(
            _request(tmp_path, "github"),
            transport=_github_transport(items, raw_json=case == "nonfinite"),
        )


def test_github_optional_model_is_validated_but_not_retained(tmp_path: Path) -> None:
    items = _github_usage_items()
    items[0]["model"] = "documented-optional-model"
    document = acquire.acquire(
        _request(tmp_path, "github"),
        transport=_github_transport(items),
    )
    assert "documented-optional-model" not in json.dumps(document)


def test_streaming_response_cap_stops_oversized_body(tmp_path: Path) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"{" + b"x" * acquire.MAX_RESPONSE_BYTES + b"}",
        )

    with pytest.raises(acquire.CaptureHoldError, match="provider_response_too_large"):
        _ = acquire.acquire(
            _request(tmp_path, "github"),
            transport=httpx2.MockTransport(handler),
        )


@pytest.mark.parametrize("provider", ["vercel-api", "vercel-web"])
def test_vercel_hobby_focus_404_requires_dashboard(
    tmp_path: Path,
    provider: str,
) -> None:
    request_value = _request(tmp_path, "vercel-api")
    project_name = (
        "prediction-monitor-api"
        if provider == "vercel-api"
        else "prediction-monitor-web"
    )
    project_env = (
        "VERCEL_API_PROJECT_ID" if provider == "vercel-api" else "VERCEL_WEB_PROJECT_ID"
    )
    request_value = acquire.CaptureRequest(
        provider=provider,
        token=request_value.token,
        identity_envs=("VERCEL_ORG_ID", project_env),
        identity_values=request_value.identity_values,
        captured_at=request_value.captured_at,
        billing_window_start=request_value.billing_window_start,
        billing_window_end=request_value.billing_window_end,
        json_out=request_value.json_out,
    )
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.startswith("/v2/teams/"):
            return _json_response(
                {
                    "id": "team_private",
                    "slug": acquire.TEAM_SLUG,
                    "name": "Private team",
                    "billing": {"plan": "hobby"},
                }
            )
        if request.url.path.startswith("/v9/projects/"):
            return _json_response(
                {
                    "id": "project_private",
                    "name": project_name,
                    "accountId": "team_private",
                }
            )
        return httpx2.Response(404)

    document = acquire.acquire(
        request_value,
        transport=httpx2.MockTransport(handler),
    )
    payloads = cast("list[dict[str, object]]", document["official_payloads"])
    assert payloads[-1]["status"] == "dashboard_required"
    assert [request.url.path for request in requests] == [
        "/v2/teams/team_private",
        "/v9/projects/project_private",
        "/v1/billing/charges",
    ]
    assert requests[-1].url.params["teamId"] == "team_private"


def _focus_record() -> dict[str, object]:
    return {
        "BillingAccountId": "team_private",
        "ChargePeriodStart": "2026-08-01T00:00:00Z",
        "ChargePeriodEnd": "2026-08-02T00:00:00Z",
        "ResourceId": "project_private",
        "ResourceName": "prediction-monitor-api",
        "ServiceName": "Functions",
        "SkuId": "function_invocations",
        "SkuPriceId": "function_invocations-us-east-1",
        "ConsumedQuantity": 10,
        "ConsumedUnit": "invocations",
        "BilledCost": 0,
        "BillingCurrency": "USD",
    }


def _vercel_focus_transport(
    record: dict[str, object], *, newline: bool = True
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.startswith("/v2/teams/"):
            return _json_response(
                {
                    "id": "team_private",
                    "slug": acquire.TEAM_SLUG,
                    "name": "Private team",
                    "billing": {"plan": "hobby"},
                }
            )
        if request.url.path.startswith("/v9/projects/"):
            return _json_response(
                {
                    "id": "project_private",
                    "name": "prediction-monitor-api",
                    "accountId": "team_private",
                }
            )
        suffix = "\n" if newline else ""
        return httpx2.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=(json.dumps(record) + suffix).encode(),
        )

    return httpx2.MockTransport(handler)


def test_vercel_focus_success_is_schema_and_window_bound(tmp_path: Path) -> None:
    document = acquire.acquire(
        _request(tmp_path, "vercel-api"),
        transport=_vercel_focus_transport(_focus_record()),
    )
    payloads = cast("list[dict[str, object]]", document["official_payloads"])
    assert payloads[-1]["status"] == "complete"


@pytest.mark.parametrize("case", ["extra", "outside", "tuple", "truncated"])
def test_vercel_focus_rejects_open_foreign_or_truncated_records(
    tmp_path: Path,
    case: str,
) -> None:
    record = _focus_record()
    newline = True
    if case == "extra":
        record["Unexpected"] = "spill"
    elif case == "outside":
        record["ChargePeriodStart"] = "2026-07-31T00:00:00Z"
    elif case == "tuple":
        record["SkuId"] = "unreviewed_sku"
    else:
        newline = False
    with pytest.raises(acquire.CaptureHoldError):
        _ = acquire.acquire(
            _request(tmp_path, "vercel-api"),
            transport=_vercel_focus_transport(record, newline=newline),
        )


def test_supabase_is_rejected_before_zero_http_calls(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    request = acquire.CaptureRequest(
        provider="supabase",
        token="forbidden-token-sentinel",
        identity_envs=("SUPABASE_ORG_ID", "SUPABASE_PROJECT_ID"),
        identity_values=("private-org", "private-project"),
        captured_at="2026-08-02T12:00:00Z",
        billing_window_start="2026-08-01T00:00:00Z",
        billing_window_end="2026-09-01T00:00:00Z",
        json_out=tmp_path / "official-payloads.json",
    )
    with pytest.raises(acquire.CaptureHoldError, match="unsupported_provider"):
        _ = acquire.acquire(request, transport=httpx2.MockTransport(handler))
    assert calls == 0


def test_main_success_has_no_stdout_or_stderr_and_owner_private_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "private" / "official-payloads.json"
    output.parent.mkdir(mode=0o700)
    monkeypatch.setenv("GITHUB_TOKEN_TEST", "private-token-sentinel")
    monkeypatch.setenv("GITHUB_ID_TEST", "123")
    if os.name == "nt":
        monkeypatch.setattr(acquire, "_windows_acl_owner_only", lambda _path: True)
        monkeypatch.setattr(acquire, "_harden_windows", lambda _path: None)
    monkeypatch.setattr(
        acquire,
        "acquire",
        lambda _request: {
            "schema": acquire.SCHEMA,
            "provider": "github",
            "captured_at": "2026-08-02T12:00:00Z",
            "billing_window_start": "2026-08-01T00:00:00Z",
            "billing_window_end": "2026-09-01T00:00:00Z",
            "identity_bindings": [],
            "official_payloads": [{"kind": "synthetic-private-test"}],
        },
    )

    result = acquire.main(
        [
            "capture",
            "--provider",
            "github",
            "--github-token-env",
            "GITHUB_TOKEN_TEST",
            "--github-repository-id-env",
            "GITHUB_ID_TEST",
            "--capture-at",
            "2026-08-02T12:00:00Z",
            "--billing-window-start",
            "2026-08-01T00:00:00Z",
            "--billing-window-end",
            "2026-09-01T00:00:00Z",
            "--json-out",
            str(output),
        ]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert output.exists()
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0
    assert "private-token-sentinel" not in output.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-sharing contract")
def test_windows_directory_handle_blocks_parent_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "private-parent"
    parent.mkdir()
    replacement_name = tmp_path / "moved-parent"

    with private_lock.hold_private_directory(parent), pytest.raises(PermissionError):
        _ = parent.rename(replacement_name)

    assert parent.is_dir()
    assert not replacement_name.exists()


def test_main_body_free_failure_deletes_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "official-payloads.json"
    monkeypatch.setenv("GITHUB_TOKEN_TEST", "raw-secret-body")
    monkeypatch.setenv("GITHUB_ID_TEST", "123")
    monkeypatch.setattr(
        acquire,
        "acquire",
        lambda _request: (_ for _ in ()).throw(
            acquire.CaptureHoldError("github_billing_scope_missing")
        ),
    )

    result = acquire.main(
        [
            "capture",
            "--provider",
            "github",
            "--github-token-env",
            "GITHUB_TOKEN_TEST",
            "--github-repository-id-env",
            "GITHUB_ID_TEST",
            "--capture-at",
            "2026-08-02T12:00:00Z",
            "--billing-window-start",
            "2026-08-01T00:00:00Z",
            "--billing-window-end",
            "2026-09-01T00:00:00Z",
            "--json-out",
            str(output),
        ]
    )

    assert result == 42
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "provider-capture HOLD: github_billing_scope_missing\n"
    assert "raw-secret-body" not in captured.err
    assert not output.exists()
