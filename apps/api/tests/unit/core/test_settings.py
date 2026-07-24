# ruff: noqa: INP001
from __future__ import annotations

import json

import pytest
from app.core.settings import IdentitySettings
from pydantic import ValidationError


def _valid_settings() -> dict[str, str]:
    return {
        "api_base_url": "https://api.example.test",
        "service_token_key_id": "primary-2026-07",
        "service_token_issuer_private_key": "private-key-material-not-logged-0001",
        "service_token_issuer_public_key": "public-key-material-not-logged-00001",
        "bff_client_credential": "bff-secret-material-not-logged-000001",
        "bff_credential_version": "bff-v1",
        "worker_bootstrap_secret": "worker-secret-material-not-logged-001",
        "worker_credential_version": "worker-v1",
        "cron_secret": "cron-secret-material-not-logged-00001",
        "admin_password_argon2id_hash": (
            "$argon2id$v=19$m=65536,t=3,p=4$"
            "c29tZXNhbHQ$RHVtbXlIYXNoVGhhdElzTG9uZ0Vub3VnaA"
        ),
        "session_hmac_secret": "session-secret-material-not-logged-001",
        "github_repository": "owner/monitor",
        "github_workflow_refs": (
            '["owner/monitor/.github/workflows/collect.yml@refs/heads/main"]'
        ),
        "github_allowed_refs": '["refs/heads/main"]',
        "github_allowed_environments": '["production"]',
    }


def test_settings_fail_closed_when_security_secret_is_blank() -> None:
    # Given
    values = _valid_settings()
    values["cron_secret"] = ""

    # When / Then
    with pytest.raises(ValidationError):
        _ = IdentitySettings.model_validate(values)


def test_settings_fail_closed_when_required_environment_is_missing() -> None:
    # Given
    values = _valid_settings()
    del values["api_base_url"]

    # When / Then
    with pytest.raises(ValidationError):
        _ = IdentitySettings.model_validate(values)


def test_settings_redact_all_secret_values() -> None:
    # Given
    values = _valid_settings()
    settings = IdentitySettings.model_validate(values)

    # When
    rendered = "\n".join(
        (
            repr(settings),
            settings.model_dump_json(),
            json.dumps(settings.redacted_metadata()),
        )
    )

    # Then
    for field in (
        "service_token_issuer_private_key",
        "bff_client_credential",
        "worker_bootstrap_secret",
        "cron_secret",
        "session_hmac_secret",
    ):
        assert values[field] not in rendered


def test_settings_reject_public_browser_credential_names() -> None:
    # Given
    values = _valid_settings() | {
        "next_public_bff_client_credential": "must-never-be-accepted"
    }

    # When / Then
    with pytest.raises(ValidationError):
        _ = IdentitySettings.model_validate(values)
