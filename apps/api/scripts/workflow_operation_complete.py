"""Complete one claimed GitHub workflow without exposing its OIDC token."""

# pyright: reportAny=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Never

MAX_RESPONSE_BYTES = 65_536


class CompletionError(RuntimeError):
    """Stable public-safe completion failure."""


def _reject(code: str) -> Never:
    raise CompletionError(code)


def _post_json(url: str, payload: dict[str, object], token: str) -> bytes:
    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS provider URLs
        url,
        data=json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read(65_537)


def _oidc_token() -> str:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not request_url or not request_token:
        _reject("oidc_environment_missing")
    separator = "&" if "?" in request_url else "?"
    request = urllib.request.Request(  # noqa: S310 - GitHub-owned ephemeral URL
        f"{request_url}{separator}audience=monitor-control",
        headers={"Authorization": f"bearer {request_token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read(65_537)
    try:
        value = json.loads(raw)["value"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        error_code = "oidc_response_invalid"
        raise CompletionError(error_code) from error
    if not isinstance(value, str) or not value:
        _reject("oidc_response_invalid")
    return value


def parser() -> argparse.ArgumentParser:
    """Build the schema-closed workflow completion CLI parser."""
    root = argparse.ArgumentParser()
    for name in (
        "api-url",
        "command",
        "display-title",
        "evidence",
        "expected-plan-sha256",
        "head-sha",
        "activation-nonce",
        "dispatch-nonce",
        "reservation-sha256",
        "workflow",
        "json-out",
    ):
        _ = root.add_argument(f"--{name}", required=True)
    _ = root.add_argument("--environment")
    _ = root.add_argument("--attempt", required=True, type=int)
    return root


def main() -> int:
    """Authenticate, complete, validate, and write one canonical receipt."""
    try:
        args = parser().parse_args()
        evidence = Path(args.evidence).read_bytes()
        if not evidence:
            _reject("completion_evidence_empty")
        run_id = int(os.environ.get("GITHUB_RUN_ID", "0"))
        run_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "0"))
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        event = os.environ.get("GITHUB_EVENT_NAME", "")
        git_ref = os.environ.get("GITHUB_REF", "")
        payload: dict[str, object] = {
            "repository": repository,
            "workflow": args.workflow,
            "display_title": args.display_title,
            "head_sha": args.head_sha,
            "approved_plan_sha256": args.expected_plan_sha256,
            "activation_nonce": args.activation_nonce,
            "dispatch_nonce": args.dispatch_nonce,
            "reservation_sha256": args.reservation_sha256,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "event": event,
            "ref": git_ref,
            "environment": args.environment,
            "command": args.command,
            "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
            "outcome": "success",
        }
        raw = _post_json(
            f"{args.api_url.rstrip('/')}/internal/release/workflow-operation-complete",
            payload,
            _oidc_token(),
        )
        if len(raw) > MAX_RESPONSE_BYTES:
            _reject("completion_response_oversize")
        document = json.loads(raw)
        if not isinstance(document, dict):
            _reject("completion_response_invalid")
        expected = {
            "accepted": True,
            "activation_nonce": args.activation_nonce,
            "approved_plan_sha256": args.expected_plan_sha256,
            "attempt": args.attempt,
            "command": args.command,
            "dispatch_nonce": args.dispatch_nonce,
            "head_sha": args.head_sha,
            "predecessor_receipt_sha256": args.reservation_sha256,
            "reservation_receipt_sha256": args.reservation_sha256,
            "retry_permitted": False,
            "reviewed_sha": args.head_sha,
            "run_id": run_id,
            "terminal_for_attempt": True,
        }
        if any(document.get(key) != value for key, value in expected.items()):
            _reject("completion_response_binding_mismatch")
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        _ = output.write_bytes(canonical)
    except (
        CompletionError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as error:
        code = str(error) if isinstance(error, CompletionError) else "completion_failed"
        _ = sys.stderr.write(f"workflow operation HOLD: {code}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
