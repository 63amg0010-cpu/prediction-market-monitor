from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pydantic import JsonValue, TypeAdapter
from scripts.source_bindings_contracts import BindingPayload

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "apps" / "api" / "scripts" / "zero_provider_binding_evidence.py"
DOCUMENT = TypeAdapter(dict[str, JsonValue])
NONCE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SOURCE_ID = "d6dc5ea1-e3af-4bfe-88ad-e4beffd22ab6"


def test_zero_provider_evidence_is_redacted_and_payload_bound(tmp_path: Path) -> None:
    bindings = [{"platform": "dcinside", "source_id": SOURCE_ID}]
    output = tmp_path / "receipt.json"
    environment = {
        **os.environ,
        "TEST_BINDINGS": json.dumps(bindings, indent=2),
    }

    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(SCRIPT),
            "--mode",
            "binding-prestate",
            "--activation-nonce",
            NONCE,
            "--source-ids",
            SOURCE_ID,
            "--scope-version",
            "phase1-reviewed-v1",
            "--bindings-json-env",
            "TEST_BINDINGS",
            "--json-out",
            str(output),
        ),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = DOCUMENT.validate_json(output.read_bytes())
    canonical_bindings = json.dumps(
        bindings,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload = BindingPayload(
        protected_json=canonical_bindings,
        source_ids=SOURCE_ID,
        scope_version="phase1-reviewed-v1",
    )
    assert receipt == {
        "accepted": True,
        "activation_nonce": NONCE,
        "mode": "binding-prestate",
        "payload_sha256": payload.sha256,
        "platforms": ["dcinside"],
        "provider_request_count": 0,
        "raw_binding_persisted": False,
        "scope_version": "phase1-reviewed-v1",
        "source_ids": SOURCE_ID,
    }
    assert canonical_bindings not in output.read_text(encoding="utf-8")


def test_zero_provider_evidence_rejects_source_id_drift(tmp_path: Path) -> None:
    output = tmp_path / "receipt.json"
    environment = {
        **os.environ,
        "TEST_BINDINGS": json.dumps(
            [{"platform": "dcinside", "source_id": SOURCE_ID}]
        ),
    }

    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(SCRIPT),
            "--mode",
            "binding-handshake",
            "--activation-nonce",
            NONCE,
            "--source-ids",
            "11111111-1111-4111-8111-111111111111",
            "--scope-version",
            "phase1-reviewed-v1",
            "--bindings-json-env",
            "TEST_BINDINGS",
            "--json-out",
            str(output),
        ),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "binding_source_ids_mismatch" in completed.stderr
    assert not output.exists()
