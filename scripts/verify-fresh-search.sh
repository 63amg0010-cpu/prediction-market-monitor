#!/usr/bin/env bash
set -euo pipefail

attempt_dir=""
database_admin_url_env=""
database_url_env=""
base_sha=""
reviewed_sha=""
failure_fixture=""
expect_meta_failure=0

while (($#)); do
  case "$1" in
    --attempt-dir) attempt_dir="$2"; shift 2 ;;
    --database-admin-url-env) database_admin_url_env="$2"; shift 2 ;;
    --database-url-env) database_url_env="$2"; shift 2 ;;
    --base-sha) base_sha="$2"; shift 2 ;;
    --reviewed-sha) reviewed_sha="$2"; shift 2 ;;
    --failure-fixture) failure_fixture="$2"; shift 2 ;;
    --expect-meta-failure) expect_meta_failure=1; shift ;;
    *) printf 'local QA HOLD: unsupported argument\n' >&2; exit 2 ;;
  esac
done

arguments=(
  apps/api/scripts/local_qa_orchestrator.py
  --attempt-dir "$attempt_dir"
  --database-admin-url-env "$database_admin_url_env"
  --database-url-env "$database_url_env"
  --base-sha "$base_sha"
  --reviewed-sha "$reviewed_sha"
  --wrapper git-bash
)
if [[ -n "$failure_fixture" ]]; then
  arguments+=(--failure-fixture "$failure_fixture")
fi
if ((expect_meta_failure)); then
  arguments+=(--expect-meta-failure)
fi

uv run --no-sync --package monitor-api python "${arguments[@]}"
