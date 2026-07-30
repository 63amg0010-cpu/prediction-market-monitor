from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

from scripts.release_vercel_models import canonical_bytes, seal_receipt
from scripts.runtime_privacy_adapter import PrivacyRuntimeError
from scripts.runtime_privacy_adapter_cli import (
    HANDLERS,
    PrivacyArgs,
    verified_predecessor,
)
from scripts.runtime_privacy_adapter_cli_validation import (
    MatrixArgs,
    matrix_proof,
)
from tests.unit.scripts.todo11_privacy_adapter_fakes import NOW
from tests.unit.scripts.todo11_privacy_stubs import scope


def _privacy_args(path: Path) -> PrivacyArgs:
    return cast(
        "PrivacyArgs",
        cast(
            "object",
            SimpleNamespace(
                predecessor_receipt=str(path),
                expected_sha="a" * 40,
                expected_plan_sha256="b" * 64,
                activation_nonce=str(scope().activation_nonce),
            ),
        ),
    )


def _common() -> dict[str, object]:
    return {
        "reviewed_sha": "a" * 40,
        "approved_plan_sha256": "b" * 64,
        "activation_nonce": str(scope().activation_nonce),
        "accepted": True,
    }


def test_predecessor_requires_canonical_self_hash_and_acceptance(
    tmp_path: Path,
) -> None:
    predecessor = seal_receipt(_common())
    path = tmp_path / "predecessor.json"
    _ = path.write_bytes(canonical_bytes(predecessor))
    assert verified_predecessor(_privacy_args(path)) == predecessor["receipt_sha256"]

    forged = {**predecessor, "accepted": False}
    _ = path.write_bytes(canonical_bytes(forged))
    with pytest.raises(
        Exception,
        match=r"receipt_hash_mismatch|predecessor_not_accepted",
    ):
        _ = verified_predecessor(_privacy_args(path))

    _ = path.write_bytes(canonical_bytes(predecessor) + b"\n")
    with pytest.raises(PrivacyRuntimeError, match="noncanonical"):
        _ = verified_predecessor(_privacy_args(path))


def _matrix_args(health: Path, chain: Path) -> MatrixArgs:
    return cast(
        "MatrixArgs",
        cast(
            "object",
            SimpleNamespace(
                matrix_b_health=str(health),
                matrix_b_chain=str(chain),
                predecessor_receipt=str(chain),
                expected_sha="a" * 40,
                expected_plan_sha256="b" * 64,
                activation_nonce=str(scope().activation_nonce),
                expected_current="20260727_0010",
                violation_kind="privacy",
            ),
        ),
    )


def test_matrix_uses_materialized_details_and_registry_exports(
    tmp_path: Path,
) -> None:
    health = seal_receipt(
        {
            **_common(),
            "command": "matrix-b-health",
            "state_after": "restore_writing",
            "database_revision": "20260727_0010",
        }
    )
    details = {
        "terminal_command": "matrix-b-health",
        "nodes": [{"receipt_sha256": health["receipt_sha256"]}],
    }
    chain = seal_receipt(
        {
            **_common(),
            "schema": "release-chain-receipt.v1",
            "command": "materialize-chain",
            "approval_round_id": "c" * 64,
            "approval_launch_sha256s": ["d" * 64, "e" * 64],
            "dispatch_nonce": None,
            "attempt": 0,
            "database_timestamps": {"created_at_db": NOW.isoformat()},
            "terminal_for_attempt": True,
            "retry_permitted": False,
            "predecessor_receipt_sha256": "f" * 64,
            "details": details,
        }
    )
    health_path, chain_path = tmp_path / "health.json", tmp_path / "chain.json"
    _ = health_path.write_bytes(canonical_bytes(health))
    _ = chain_path.write_bytes(canonical_bytes(chain))
    assert (
        matrix_proof(_matrix_args(health_path, chain_path)).receipt_sha256
        == (chain["receipt_sha256"])
    )
    assert set(HANDLERS) == {
        "privacy-contain",
        "privacy-purge",
        "privacy-verify",
    }

    chain["details"] = {**details, "nodes": []}
    _ = chain_path.write_bytes(canonical_bytes(seal_receipt(chain)))
    with pytest.raises(PrivacyRuntimeError, match="matrix_b_privacy_proof_invalid"):
        _ = matrix_proof(_matrix_args(health_path, chain_path))
