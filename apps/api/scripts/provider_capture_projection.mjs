/** Schema-closed Browser projection for owner-private provider quota captures. */

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { constants, promises as fs } from "node:fs";
import { userInfo } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";

const WINDOWS_LOCK_HELPER = fileURLToPath(new URL("./private_windows_directory_lock.py", import.meta.url));
const IS_WINDOWS = path.sep === "\\";
const REPOSITORY_PYTHON = path.resolve(path.dirname(WINDOWS_LOCK_HELPER), "../../../.venv/Scripts/python.exe");
const WINDOWS_LOCK_HELPER_SHA256 = "6f6f62967c1943a637e361202205972e5417ff06f5c9f4a76162b77b28644cd2";

const OFFICIAL_SCHEMA = "free-tier.provider-official-payloads.v1";
const OBSERVATION_SCHEMA = "free-tier.provider-observation.v1";
const RESPONSE_SCHEMA = "free-tier.provider-private-response.v1";
const PROVIDERS = new Set(["github", "vercel-api", "vercel-web", "supabase"]);
const PUBLIC_PROJECTS = {
  github: "63amg0010-cpu/prediction-market-monitor",
  "vercel-api": "prediction-monitor-api",
  "vercel-web": "prediction-monitor-web",
  supabase: "redacted-supabase-project",
};
const EXPECTED_DIMENSIONS = {
  github: new Set([
    "github_actions_minutes",
    "github_artifact_gb_hours",
    "github_packages_gb_hours",
    "github_cache_bytes",
  ]),
  "vercel-api": new Set(["vercel_api_invocations"]),
  "vercel-web": new Set([
    "vercel_web_invocations",
    "vercel_cpu_ms",
    "vercel_memory_gb_seconds",
    "vercel_transfer_bytes",
    "vercel_deployments",
  ]),
  supabase: new Set([
    "supabase_database_bytes",
    "supabase_uncached_egress_bytes",
    "supabase_cached_egress_bytes",
    "supabase_storage_bytes",
    "supabase_mau",
    "supabase_edge_invocations",
    "supabase_realtime_messages",
  ]),
};
const SUPABASE_EXCLUSIONS = new Map([
  [
    "supabase_disk_iops_addon",
    {
      reason: "provisioned_disk_addon_not_enabled",
      url: "https://supabase.com/docs/guides/platform/manage-your-usage/disk-iops",
    },
  ],
  [
    "supabase_disk_throughput_addon",
    {
      reason: "provisioned_disk_addon_not_enabled",
      url: "https://supabase.com/docs/guides/platform/manage-your-usage/disk-throughput",
    },
  ],
  [
    "supabase_logs_ingest",
    {
      reason: "billing_enforcement_not_live",
      url: "https://supabase.com/docs/guides/platform/manage-your-usage/logs",
    },
  ],
]);
const POLICY_MAX_AGE_MS = 2 * 60 * 60 * 1000;
const IDENTITY_ENVS = {
  github: ["GITHUB_REPOSITORY_ID"],
  "vercel-api": ["VERCEL_ORG_ID", "VERCEL_API_PROJECT_ID"],
  "vercel-web": ["VERCEL_ORG_ID", "VERCEL_WEB_PROJECT_ID"],
  supabase: ["SUPABASE_ORG_ID", "SUPABASE_PROJECT_ID"],
};
const ALL_IDENTITY_ENVS = new Set(Object.values(IDENTITY_ENVS).flat());
let privateIdentityValues = null;
const EXPECTED_PLANS = {
  github: "public-standard",
  "vercel-api": "hobby",
  "vercel-web": "hobby",
  supabase: "free",
};
const GITHUB_ITEM_FIELDS = new Set([
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
]);
const GITHUB_TUPLES = new Map([
  ["Actions\0actions_linux\0minutes", "github_actions_minutes"],
  ["Actions\0actions_windows\0minutes", "github_actions_minutes"],
  ["Actions\0actions_macos\0minutes", "github_actions_minutes"],
  ["Actions\0actions_storage\0gigabyte-hours", "github_artifact_gb_hours"],
  ["Packages\0packages_storage\0gigabyte-hours", "github_packages_gb_hours"],
]);
const FOCUS_FIELDS = new Set([
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
]);
const VERCEL_TUPLES = {
  "vercel-api": new Map([
    ["Functions\0function_invocations\0invocations", "vercel_api_invocations"],
  ]),
  "vercel-web": new Map([
    ["Functions\0function_invocations\0invocations", "vercel_web_invocations"],
    ["Functions\0function_cpu\0milliseconds", "vercel_cpu_ms"],
    ["Functions\0function_memory\0gigabyte-seconds", "vercel_memory_gb_seconds"],
    ["Data Transfer\0edge_transfer\0bytes", "vercel_transfer_bytes"],
    ["Builds\0deployments\0deployments", "vercel_deployments"],
  ]),
};
const PRIVATE_DOCUMENT_FIELDS = new Set([
  "schema",
  "provider",
  "captured_at",
  "billing_window_start",
  "billing_window_end",
  "identity_bindings",
  "official_payloads",
]);
const OPERAND_FIELDS = new Set([
  "traffic",
  "workflow_attempts",
  "deployment_attempts",
  "artifacts",
  "encrypted_backup",
]);
const GIB = 1024 * 1024 * 1024;
const HORIZON_MS = 30 * 24 * 60 * 60 * 1000;
const DOM_DIMENSIONS = {
  github: {
    github_actions_minutes: { labels: ["Actions minutes", "Actions usage"], unit: "minutes" },
    github_artifact_gb_hours: { labels: ["Artifact storage", "Artifacts storage"], unit: "gigabyte-hours" },
    github_packages_gb_hours: { labels: ["Packages storage", "Package storage"], unit: "gigabyte-hours" },
    github_cache_bytes: { labels: ["Actions cache", "Cache storage"], unit: "bytes" },
  },
  "vercel-api": {
    vercel_api_invocations: { labels: ["Function Invocations", "Invocations"], unit: "invocations" },
  },
  "vercel-web": {
    vercel_web_invocations: { labels: ["Function Invocations", "Invocations"], unit: "invocations" },
    vercel_cpu_ms: { labels: ["CPU Duration", "CPU time"], unit: "milliseconds" },
    vercel_memory_gb_seconds: { labels: ["Memory Duration", "Provisioned Memory"], unit: "gigabyte-seconds" },
    vercel_transfer_bytes: { labels: ["Fast Data Transfer", "Data Transfer"], unit: "bytes" },
    vercel_deployments: { labels: ["Deployments", "Builds"], unit: "deployments" },
  },
  supabase: {
    supabase_database_bytes: { labels: ["Database Size"], unit: "bytes" },
    supabase_uncached_egress_bytes: { labels: ["Egress (uncached)", "Uncached Egress"], unit: "bytes" },
    supabase_cached_egress_bytes: { labels: ["Cached Egress"], unit: "bytes" },
    supabase_storage_bytes: { labels: ["Storage Size", "Storage"], unit: "bytes" },
    supabase_mau: { labels: ["Monthly Active Users", "MAU"], unit: "users" },
    supabase_edge_invocations: { labels: ["Edge Function Invocations"], unit: "invocations" },
    supabase_realtime_messages: { labels: ["Realtime Messages"], unit: "messages" },
  },
};

function hold(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function ambientEnvironmentValue(name) {
  return typeof process !== "undefined" && process?.env ? process.env[name] : undefined;
}

function identityValue(name) {
  const value = privateIdentityValues?.[name] ?? ambientEnvironmentValue(name);
  if (typeof value !== "string" || value.length === 0) hold("identity_binding_mismatch");
  return value;
}

export function installPrivateIdentityValues(values) {
  exactKeys(values, ALL_IDENTITY_ENVS, "private_identity_values_invalid");
  const checked = {};
  for (const name of ALL_IDENTITY_ENVS) {
    checked[name] = nonemptyString(values[name], "private_identity_values_invalid");
  }
  privateIdentityValues = Object.freeze(checked);
}

export function clearPrivateIdentityValues() {
  privateIdentityValues = null;
}

function exactKeys(value, expected, code) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) hold(code);
  const keys = Object.keys(value);
  if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) hold(code);
  return value;
}

function sha256Bytes(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalize(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) hold("canonical_number_invalid");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (typeof value !== "object") hold("canonical_type_invalid");
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
    .join(",")}}`;
}

function parseUtc(value, code) {
  if (typeof value !== "string" || !value.endsWith("Z")) hold(code);
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) hold(code);
  return timestamp;
}

function uint(value, code) {
  if (!Number.isSafeInteger(value) || value < 0) hold(code);
  return value;
}

function nonnegativeNumber(value, code) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) hold(code);
  return value;
}

function nonemptyString(value, code) {
  if (typeof value !== "string" || value.length === 0) hold(code);
  return value;
}

function addUsage(target, dimension, value) {
  target.set(dimension, (target.get(dimension) ?? 0) + value);
}

function expectedDashboardUrl(provider) {
  if (provider === "github") return "https://github.com/settings/billing/usage";
  if (provider === "vercel-api" || provider === "vercel-web") {
    return "https://vercel.com/63amg0010-5358s-projects/~/usage";
  }
  const organization = identityValue("SUPABASE_ORG_ID");
  return `https://supabase.com/dashboard/org/${encodeURIComponent(organization)}/usage`;
}

function requireIdentityBindings(document, provider, identityEnvNames) {
  const expectedNames = IDENTITY_ENVS[provider];
  if (
    !Array.isArray(identityEnvNames) ||
    identityEnvNames.length !== expectedNames.length ||
    identityEnvNames.some((name, index) => name !== expectedNames[index])
  ) {
    hold("identity_env_names_invalid");
  }
  if (!Array.isArray(document.identity_bindings) || document.identity_bindings.length !== expectedNames.length) {
    hold("identity_bindings_invalid");
  }
  document.identity_bindings.forEach((binding, index) => {
    exactKeys(binding, new Set(["env", "sha256"]), "identity_bindings_invalid");
    const name = expectedNames[index];
    const secret = identityValue(name);
    if (!secret || binding.env !== name || binding.sha256 !== sha256Bytes(Buffer.from(secret))) {
      hold("identity_binding_mismatch");
    }
  });
}

function validateGithubOfficial(document) {
  const payloads = document.official_payloads;
  if (!Array.isArray(payloads) || payloads.length !== 4) hold("github_official_payloads_invalid");
  const [repositoryPayload, artifactsPayload, cachePayload, billingPayload] = payloads;
  exactKeys(repositoryPayload, new Set(["kind", "value"]), "github_repository_payload_invalid");
  if (repositoryPayload.kind !== "repository") hold("github_repository_payload_invalid");
  const repository = exactKeys(
    repositoryPayload.value,
    new Set(["id", "full_name", "private"]),
    "github_repository_payload_invalid",
  );
  if (
    !Number.isSafeInteger(repository.id) ||
    String(repository.id) !== identityValue("GITHUB_REPOSITORY_ID") ||
    repository.full_name !== PUBLIC_PROJECTS.github ||
    repository.private !== false
  ) {
    hold("github_repository_identity_mismatch");
  }

  exactKeys(artifactsPayload, new Set(["kind", "value"]), "github_artifacts_payload_invalid");
  if (artifactsPayload.kind !== "artifacts" || !Array.isArray(artifactsPayload.value)) {
    hold("github_artifacts_payload_invalid");
  }
  const artifactIds = new Set();
  for (const artifact of artifactsPayload.value) {
    exactKeys(
      artifact,
      new Set(["id", "size_in_bytes", "created_at", "expires_at", "expired"]),
      "github_artifacts_payload_invalid",
    );
    const artifactId = uint(artifact.id, "github_artifacts_payload_invalid");
    if (
      artifactIds.has(artifactId) ||
      typeof artifact.created_at !== "string" ||
      typeof artifact.expires_at !== "string" ||
      typeof artifact.expired !== "boolean"
    ) {
      hold("github_artifacts_payload_invalid");
    }
    artifactIds.add(artifactId);
    uint(artifact.size_in_bytes, "github_artifacts_payload_invalid");
    parseUtc(artifact.created_at, "github_artifacts_payload_invalid");
    parseUtc(artifact.expires_at, "github_artifacts_payload_invalid");
  }

  exactKeys(cachePayload, new Set(["kind", "value"]), "github_cache_payload_invalid");
  if (cachePayload.kind !== "cache-usage") hold("github_cache_payload_invalid");
  const cache = exactKeys(
    cachePayload.value,
    new Set(["active_caches_size_in_bytes", "active_caches_count"]),
    "github_cache_payload_invalid",
  );
  const usage = new Map([
    ["github_cache_bytes", uint(cache.active_caches_size_in_bytes, "github_cache_payload_invalid")],
  ]);
  uint(cache.active_caches_count, "github_cache_payload_invalid");

  exactKeys(
    billingPayload,
    new Set(["kind", "request_scope", "time_period", "value"]),
    "github_billing_payload_invalid",
  );
  if (billingPayload.kind !== "billing-summary" || !Array.isArray(billingPayload.value)) {
    hold("github_billing_payload_invalid");
  }
  const captured = new Date(document.captured_at);
  const year = captured.getUTCFullYear();
  const month = captured.getUTCMonth() + 1;
  const scope = exactKeys(
    billingPayload.request_scope,
    new Set(["year", "month", "repository"]),
    "github_billing_scope_invalid",
  );
  const period = exactKeys(
    billingPayload.time_period,
    new Set(["year", "month"]),
    "github_billing_scope_invalid",
  );
  if (
    scope.year !== year ||
    scope.month !== month ||
    scope.repository !== PUBLIC_PROJECTS.github ||
    period.year !== year ||
    period.month !== month
  ) {
    hold("github_billing_scope_invalid");
  }
  for (const item of billingPayload.value) {
    exactKeys(item, GITHUB_ITEM_FIELDS, "github_billing_item_invalid");
    for (const field of GITHUB_ITEM_FIELDS) {
      if (["product", "sku", "unitType"].includes(field)) nonemptyString(item[field], "github_billing_item_invalid");
      else nonnegativeNumber(item[field], "github_billing_item_invalid");
    }
    const dimension = GITHUB_TUPLES.get(`${item.product}\0${item.sku}\0${item.unitType}`);
    if (!dimension) hold("github_billing_tuple_unknown");
    addUsage(usage, dimension, item.netQuantity);
  }
  return usage;
}

function validateVercelOfficial(document, provider) {
  const payloads = document.official_payloads;
  if (!Array.isArray(payloads) || payloads.length !== 3) hold("vercel_official_payloads_invalid");
  const [teamPayload, projectPayload, focusPayload] = payloads;
  exactKeys(teamPayload, new Set(["kind", "value"]), "vercel_team_payload_invalid");
  if (teamPayload.kind !== "team") hold("vercel_team_payload_invalid");
  const team = exactKeys(
    teamPayload.value,
    new Set(["id", "slug", "name", "billing"]),
    "vercel_team_payload_invalid",
  );
  const billing = exactKeys(team.billing, new Set(["plan"]), "vercel_team_payload_invalid");
  if (
    team.id !== identityValue("VERCEL_ORG_ID") ||
    team.slug !== "63amg0010-5358s-projects" ||
    !nonemptyString(team.name, "vercel_team_payload_invalid") ||
    billing.plan !== "hobby"
  ) {
    hold("vercel_team_identity_mismatch");
  }
  exactKeys(projectPayload, new Set(["kind", "value"]), "vercel_project_payload_invalid");
  if (projectPayload.kind !== "project") hold("vercel_project_payload_invalid");
  const project = exactKeys(
    projectPayload.value,
    new Set(["id", "name", "accountId"]),
    "vercel_project_payload_invalid",
  );
  if (
    project.id !== identityValue(IDENTITY_ENVS[provider][1]) ||
    project.name !== PUBLIC_PROJECTS[provider] ||
    project.accountId !== identityValue("VERCEL_ORG_ID")
  ) {
    hold("vercel_project_identity_mismatch");
  }
  exactKeys(
    focusPayload,
    new Set(["kind", "status", "request_scope", "value"]),
    "vercel_focus_payload_invalid",
  );
  if (focusPayload.kind !== "focus-billing" || !Array.isArray(focusPayload.value)) {
    hold("vercel_focus_payload_invalid");
  }
  const scope = exactKeys(
    focusPayload.request_scope,
    new Set(["teamId", "from", "to"]),
    "vercel_focus_scope_invalid",
  );
  if (
    scope.teamId !== identityValue("VERCEL_ORG_ID") ||
    scope.from !== document.billing_window_start ||
    scope.to !== document.billing_window_end
  ) {
    hold("vercel_focus_scope_invalid");
  }
  if (focusPayload.status === "dashboard_required") {
    if (focusPayload.value.length !== 0) hold("vercel_focus_payload_invalid");
    return null;
  }
  if (focusPayload.status !== "complete" || focusPayload.value.length === 0) {
    hold("vercel_focus_payload_invalid");
  }
  const usage = new Map();
  const present = new Set();
  const start = parseUtc(document.billing_window_start, "vercel_focus_window_invalid");
  const end = parseUtc(document.billing_window_end, "vercel_focus_window_invalid");
  for (const record of focusPayload.value) {
    exactKeys(record, FOCUS_FIELDS, "vercel_focus_record_invalid");
    for (const field of FOCUS_FIELDS) {
      if (["ConsumedQuantity", "BilledCost"].includes(field)) nonnegativeNumber(record[field], "vercel_focus_record_invalid");
      else nonemptyString(record[field], "vercel_focus_record_invalid");
    }
    const chargeStart = parseUtc(record.ChargePeriodStart, "vercel_focus_window_invalid");
    const chargeEnd = parseUtc(record.ChargePeriodEnd, "vercel_focus_window_invalid");
    if (
      record.BillingAccountId !== identityValue("VERCEL_ORG_ID") ||
      record.ResourceId !== identityValue(IDENTITY_ENVS[provider][1]) ||
      record.ResourceName !== PUBLIC_PROJECTS[provider] ||
      record.BillingCurrency !== "USD" ||
      !(start <= chargeStart && chargeStart < chargeEnd && chargeEnd <= end)
    ) {
      hold("vercel_focus_record_invalid");
    }
    const dimension = VERCEL_TUPLES[provider].get(`${record.ServiceName}\0${record.SkuId}\0${record.ConsumedUnit}`);
    if (!dimension) hold("vercel_focus_tuple_unknown");
    present.add(dimension);
    addUsage(usage, dimension, record.ConsumedQuantity);
  }
  const required = EXPECTED_DIMENSIONS[provider];
  if (present.size !== required.size || [...present].some((value) => !required.has(value))) {
    hold("vercel_focus_dimension_missing");
  }
  return usage;
}

function validateSupabaseExclusions(exclusions, capturedAt, accountStatus) {
  if (!Array.isArray(exclusions) || exclusions.length !== SUPABASE_EXCLUSIONS.size) {
    hold("supabase_policy_evidence_incomplete");
  }
  const captured = parseUtc(capturedAt, "captured_at_invalid");
  const accountStatusSha256 = sha256Bytes(Buffer.from(canonicalize(accountStatus)));
  const names = new Set();
  for (const exclusion of exclusions) {
    exactKeys(
      exclusion,
      new Set([
        "name",
        "status",
        "reason_code",
        "policy_url",
        "policy_sha256",
        "retrieved_at",
        "account_status_sha256",
      ]),
      "supabase_policy_evidence_invalid",
    );
    const contract = SUPABASE_EXCLUSIONS.get(exclusion.name);
    const retrieved = parseUtc(exclusion.retrieved_at, "supabase_policy_evidence_invalid");
    if (
      !contract ||
      names.has(exclusion.name) ||
      exclusion.status !== "not_applicable" ||
      exclusion.reason_code !== contract.reason ||
      exclusion.policy_url !== contract.url ||
      !/^[0-9a-f]{64}$/u.test(exclusion.policy_sha256) ||
      exclusion.account_status_sha256 !== accountStatusSha256 ||
      !(retrieved <= captured && captured < retrieved + POLICY_MAX_AGE_MS)
    ) {
      hold("supabase_policy_evidence_invalid");
    }
    names.add(exclusion.name);
  }
  if (names.size !== SUPABASE_EXCLUSIONS.size) hold("supabase_policy_evidence_incomplete");
  return exclusions;
}

function validateSupabaseOfficial(document) {
  const payloads = document.official_payloads;
  if (!Array.isArray(payloads) || payloads.length !== 1) hold("supabase_official_payload_invalid");
  const payload = exactKeys(payloads[0], new Set(["kind", "value"]), "supabase_official_payload_invalid");
  if (payload.kind !== "supabase-dashboard") hold("supabase_official_payload_invalid");
  const value = exactKeys(
    payload.value,
    new Set([
      "plan",
      "paid_enabled",
      "overage_enabled",
      "addon_enabled",
      "project_filter",
      "billing_window_start",
      "billing_window_end",
      "source_url",
      "dimensions",
      "non_applicable_dimensions",
      "connector_bindings",
    ]),
    "supabase_official_payload_invalid",
  );
  if (
    value.plan !== "free" ||
    value.paid_enabled !== false ||
    value.overage_enabled !== false ||
    value.addon_enabled !== false ||
    value.project_filter !== identityValue("SUPABASE_PROJECT_ID") ||
    value.billing_window_start !== document.billing_window_start ||
    value.billing_window_end !== document.billing_window_end ||
    value.source_url !== expectedDashboardUrl("supabase") ||
    !Array.isArray(value.dimensions) ||
    !Array.isArray(value.connector_bindings) ||
    value.connector_bindings.length !== 4 ||
    value.connector_bindings.some((binding) => typeof binding !== "string" || !/^[0-9a-f]{64}$/u.test(binding))
  ) {
    hold("supabase_official_payload_invalid");
  }
  const usage = new Map();
  for (const dimension of value.dimensions) {
    exactKeys(dimension, new Set(["name", "value", "unit", "quota"]), "supabase_official_dimension_invalid");
    if (!EXPECTED_DIMENSIONS.supabase.has(dimension.name) || usage.has(dimension.name)) {
      hold("supabase_official_dimension_invalid");
    }
    usage.set(dimension.name, uint(dimension.value, "supabase_official_dimension_invalid"));
    uint(dimension.quota, "supabase_official_dimension_invalid");
    nonemptyString(dimension.unit, "supabase_official_dimension_invalid");
  }
  if (usage.size !== EXPECTED_DIMENSIONS.supabase.size) hold("supabase_official_dimension_missing");
  const accountStatus = {
    plan: value.plan,
    paid_enabled: value.paid_enabled,
    overage_enabled: value.overage_enabled,
    addon_enabled: value.addon_enabled,
  };
  return {
    usage,
    exclusions: validateSupabaseExclusions(
      value.non_applicable_dimensions,
      document.captured_at,
      accountStatus,
    ),
  };
}

function validateOfficialDocument(document, provider, identityEnvNames) {
  exactKeys(document, PRIVATE_DOCUMENT_FIELDS, "official_payload_schema_open");
  if (document.schema !== OFFICIAL_SCHEMA || document.provider !== provider) {
    hold("official_payload_identity_invalid");
  }
  requireIdentityBindings(document, provider, identityEnvNames);
  const start = parseUtc(document.billing_window_start, "official_window_invalid");
  const captured = parseUtc(document.captured_at, "official_window_invalid");
  const end = parseUtc(document.billing_window_end, "official_window_invalid");
  if (!(start < captured && captured < end)) hold("official_window_invalid");
  const validated =
    provider === "github"
      ? validateGithubOfficial(document)
      : provider === "supabase"
        ? validateSupabaseOfficial(document)
        : validateVercelOfficial(document, provider);
  return provider === "supabase"
    ? { document, usage: validated.usage, exclusions: validated.exclusions }
    : { document, usage: validated, exclusions: null };
}

function windowsToolEnvironment(extra = {}) {
  const result = { ...extra };
  for (const name of ["SystemRoot", "WINDIR", "PATH", "PATHEXT", "ComSpec"]) {
    const value = ambientEnvironmentValue(name);
    if (value) result[name] = value;
  }
  return result;
}

function windowsAclOwnerOnly(filePath) {
  const childOptions = { encoding: "utf8", windowsHide: true, env: windowsToolEnvironment() };
  const identityResult = spawnSync("whoami.exe", [], childOptions);
  const aclResult = spawnSync("icacls.exe", [filePath], childOptions);
  if (identityResult.status !== 0 || aclResult.status !== 0) return false;
  const identity = identityResult.stdout.trim().toLowerCase();
  const lines = aclResult.stdout
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter((line) => /:\((?:[^()]|\([^)]*\))*\)/u.test(line));
  return lines.length === 1 && lines[0].toLowerCase().includes(identity) && lines[0].includes("(F)") && !lines[0].includes("(I)");
}

function hardenWindowsAcl(filePath) {
  const script = [
    "$target=$env:PROVIDER_CAPTURE_ACL_TARGET",
    "$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
    "$acl=New-Object System.Security.AccessControl.FileSecurity",
    "$acl.SetAccessRuleProtection($true,$false)",
    "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule($identity,'FullControl','Allow')",
    "$acl.AddAccessRule($rule)",
    "[System.IO.File]::SetAccessControl($target,$acl)",
  ].join(";");
  const result = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    {
      encoding: "utf8",
      windowsHide: true,
      env: windowsToolEnvironment({ PROVIDER_CAPTURE_ACL_TARGET: filePath }),
    },
  );
  if (result.status !== 0 || !windowsAclOwnerOnly(filePath)) hold("private_output_acl_failed");
}

async function requirePrivateRegularFile(filePath) {
  const stats = await fs.lstat(filePath).catch(() => hold("private_file_unavailable"));
  if (!stats.isFile() || stats.isSymbolicLink() || stats.nlink !== 1 || stats.size <= 0) {
    hold("private_file_invalid");
  }
  if (IS_WINDOWS) {
    if (!windowsAclOwnerOnly(filePath)) hold("private_file_acl_invalid");
  } else if ((stats.mode & 0o077) !== 0 || stats.uid !== userInfo().uid) {
    hold("private_file_acl_invalid");
  }
}

async function requirePrivateDirectory(directoryPath) {
  const stats = await fs.lstat(directoryPath).catch(() => hold("private_root_unavailable"));
  if (!stats.isDirectory() || stats.isSymbolicLink()) hold("private_root_invalid");
  const resolved = await fs.realpath(directoryPath).catch(() => hold("private_root_unavailable"));
  if (path.resolve(resolved) !== path.resolve(directoryPath)) hold("private_root_alias_detected");
  if (IS_WINDOWS) {
    if (!windowsAclOwnerOnly(directoryPath)) hold("private_root_acl_invalid");
  } else if ((stats.mode & 0o077) !== 0 || stats.uid !== userInfo().uid) {
    hold("private_root_acl_invalid");
  }
  return stats;
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

async function withPrivateDirectoryLock(directoryPath, operation) {
  if (!IS_WINDOWS) return operation();
  const [pythonStats, helperStats, pythonRealPath, helperRealPath, helperBytes] = await Promise.all([
    fs.lstat(REPOSITORY_PYTHON).catch(() => hold("private_directory_lock_runtime_invalid")),
    fs.lstat(WINDOWS_LOCK_HELPER).catch(() => hold("private_directory_lock_runtime_invalid")),
    fs.realpath(REPOSITORY_PYTHON).catch(() => hold("private_directory_lock_runtime_invalid")),
    fs.realpath(WINDOWS_LOCK_HELPER).catch(() => hold("private_directory_lock_runtime_invalid")),
    fs.readFile(WINDOWS_LOCK_HELPER).catch(() => hold("private_directory_lock_runtime_invalid")),
  ]);
  if (
    !pythonStats.isFile() ||
    pythonStats.isSymbolicLink() ||
    path.resolve(pythonRealPath) !== REPOSITORY_PYTHON ||
    !helperStats.isFile() ||
    helperStats.isSymbolicLink() ||
    helperStats.nlink !== 1 ||
    path.resolve(helperRealPath) !== WINDOWS_LOCK_HELPER ||
    sha256Bytes(helperBytes) !== WINDOWS_LOCK_HELPER_SHA256
  ) {
    hold("private_directory_lock_runtime_invalid");
  }
  const allowedEnvironment = windowsToolEnvironment();
  allowedEnvironment.PYTHONUTF8 = "1";
  allowedEnvironment.PROVIDER_CAPTURE_LOCK_PATH = directoryPath;
  const child = spawn(REPOSITORY_PYTHON, [WINDOWS_LOCK_HELPER], {
    cwd: path.dirname(WINDOWS_LOCK_HELPER),
    env: allowedEnvironment,
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stderr.resume();
  const exit = new Promise((resolve) => child.once("exit", (code) => resolve(code)));
  const isReady = () => output === "READY\n" || output === "READY\r\n";
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("private_directory_lock_timeout")), 10_000);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.stdout.on("data", (chunk) => {
      output += chunk;
      if (!output.includes("\n")) return;
      clearTimeout(timer);
      if (isReady()) resolve();
      else reject(new Error("private_directory_lock_protocol_invalid"));
    });
    child.once("exit", (code) => {
      if (!isReady()) {
        clearTimeout(timer);
        reject(new Error(`private_directory_lock_failed:${code}`));
      }
    });
  });
  try {
    await ready;
  } catch {
    child.stdin.destroy();
    child.kill();
    await exit;
    hold("private_directory_lock_failed");
  }
  try {
    return await operation();
  } finally {
    child.stdin.write(Buffer.from([0]));
    child.stdin.end();
    const code = await exit;
    if (code !== 0) hold("private_directory_lock_failed");
  }
}

async function readPrivateFileBound(filePath) {
  const resolved = path.resolve(filePath);
  return withPrivateDirectoryLock(path.dirname(resolved), () => readPrivateFileBoundLocked(resolved));
}

async function readPrivateFileBoundLocked(filePath) {
  const resolved = path.resolve(filePath);
  const parent = path.dirname(resolved);
  const parentBefore = await requirePrivateDirectory(parent);
  const fileBefore = await fs.lstat(resolved).catch(() => hold("private_file_unavailable"));
  if (!fileBefore.isFile() || fileBefore.isSymbolicLink() || fileBefore.nlink !== 1 || fileBefore.size <= 0) {
    hold("private_file_invalid");
  }
  const noFollow = constants.O_NOFOLLOW ?? 0;
  const handle = await fs.open(resolved, constants.O_RDONLY | noFollow).catch(() => hold("private_file_open_failed"));
  try {
    const opened = await handle.stat();
    if (!sameFile(opened, fileBefore) || !opened.isFile() || opened.nlink !== 1) hold("private_file_changed");
    const bytes = await handle.readFile();
    const parentAfter = await fs.lstat(parent).catch(() => hold("private_root_changed"));
    const fileAfter = await fs.lstat(resolved).catch(() => hold("private_file_changed"));
    const realParentAfter = await fs.realpath(parent).catch(() => hold("private_root_changed"));
    if (
      !sameFile(parentBefore, parentAfter) ||
      !sameFile(opened, fileAfter) ||
      path.resolve(realParentAfter) !== parent ||
      fileAfter.isSymbolicLink() ||
      fileAfter.nlink !== 1
    ) {
      hold("private_file_changed");
    }
    await requirePrivateRegularFile(resolved);
    return bytes;
  } finally {
    await handle.close();
  }
}

export async function loadPrivateOfficialPayloads(filePath, provider, identityEnvNames) {
  if (!PROVIDERS.has(provider)) hold("provider_invalid");
  const resolved = path.resolve(filePath);
  if (path.basename(resolved) !== "official-payloads.json") hold("official_payload_path_invalid");
  const bytes = await readPrivateFileBound(resolved);
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    hold("official_payload_json_invalid");
  }
  return validateOfficialDocument(document, provider, identityEnvNames).document;
}

function insideWindow(record, start, end) {
  const possible = parseUtc(record.possible_at, "possible_at_invalid");
  return parseUtc(start, "window_invalid") <= possible && possible < parseUtc(end, "window_invalid");
}

function retentionUnits(record, byteField) {
  return Math.ceil(
    (uint(record[byteField], "retention_operand_invalid") *
      uint(record.attempts, "retention_operand_invalid") *
      uint(record.retention_hours, "retention_operand_invalid") *
      uint(record.units_per_gib_hour, "retention_operand_invalid")) /
      GIB,
  );
}

export function deriveAddedUsageRaw(dimension, capturedAt) {
  const operands = exactKeys(dimension.projection_operands, OPERAND_FIELDS, "projection_operands_invalid");
  const capture = parseUtc(capturedAt, "captured_at_invalid");
  const start = parseUtc(dimension.window_start, "window_invalid");
  const end = parseUtc(dimension.window_end, "window_invalid");
  const traffic = exactKeys(
    operands.traffic,
    new Set(["trailing_30d_page_requests", "units_per_page_request"]),
    "traffic_invalid",
  );
  const overlaps = Math.min(capture + HORIZON_MS, end) > Math.max(capture, start);
  const pageUnits = overlaps
    ? Math.max(10_000, 3 * uint(traffic.trailing_30d_page_requests, "traffic_invalid")) *
      uint(traffic.units_per_page_request, "traffic_invalid")
    : 0;
  if (!Array.isArray(operands.workflow_attempts)) hold("workflow_attempts_invalid");
  const attempts = operands.workflow_attempts.reduce((total, record) => {
    if (!insideWindow(record, dimension.window_start, dimension.window_end)) return total;
    return (
      total +
      (uint(record.max_attempts, "workflow_attempts_invalid") +
        uint(record.rejected_duplicate_orphan_attempts, "workflow_attempts_invalid")) *
        uint(record.units_per_attempt, "workflow_attempts_invalid")
    );
  }, 0);
  if (!Array.isArray(operands.deployment_attempts)) hold("deployment_attempts_invalid");
  const deployments = operands.deployment_attempts.reduce((total, record) => {
    if (!insideWindow(record, dimension.window_start, dimension.window_end)) return total;
    return (
      total +
      (uint(record.max_attempts, "deployment_attempts_invalid") +
        uint(record.successful_replacement_builds, "deployment_attempts_invalid")) *
        uint(record.units_per_attempt, "deployment_attempts_invalid")
    );
  }, 0);
  if (!Array.isArray(operands.artifacts)) hold("artifacts_invalid");
  const artifacts = operands.artifacts.reduce(
    (total, record) => total + retentionUnits(record, "raw_measured_bytes"),
    0,
  );
  const backup = operands.encrypted_backup;
  const backupBytes = Math.max(
    uint(backup.last_successful_encrypted_backup_bytes, "backup_invalid"),
    uint(backup.current_logical_size_estimate_bytes, "backup_invalid"),
  );
  if (backupBytes <= 0 || uint(backup.attempts, "backup_invalid") <= 0) hold("backup_invalid");
  return pageUnits + attempts + deployments + artifacts + retentionUnits({ ...backup, backup_bytes: backupBytes }, "backup_bytes");
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

function decimalNumber(value, code) {
  const normalized = value.replaceAll(",", "");
  if (!/^\d+(?:\.\d+)?$/u.test(normalized)) hold(code);
  const number = Number(normalized);
  if (!Number.isFinite(number) || number < 0) hold(code);
  return number;
}

function normalizeDomMeasure(value, unit, expectedUnit) {
  const number = decimalNumber(value, "dashboard_counter_invalid");
  const normalizedUnit = unit.toLowerCase().replaceAll(" ", "-");
  const byteMultipliers = {
    b: 1,
    byte: 1,
    bytes: 1,
    kb: 1000,
    mb: 1000 ** 2,
    gb: 1000 ** 3,
    tb: 1000 ** 4,
    kib: 1024,
    mib: 1024 ** 2,
    gib: 1024 ** 3,
    tib: 1024 ** 4,
  };
  let result;
  if (expectedUnit === "bytes") {
    const multiplier = byteMultipliers[normalizedUnit];
    if (!multiplier) hold("dashboard_unit_invalid");
    result = number * multiplier;
  } else if (expectedUnit === "milliseconds") {
    if (["ms", "millisecond", "milliseconds"].includes(normalizedUnit)) result = number;
    else if (["s", "second", "seconds"].includes(normalizedUnit)) result = number * 1000;
    else hold("dashboard_unit_invalid");
  } else if (expectedUnit === "gigabyte-seconds") {
    if (!["gb-s", "gb-second", "gb-seconds", "gigabyte-second", "gigabyte-seconds"].includes(normalizedUnit)) {
      hold("dashboard_unit_invalid");
    }
    result = number;
  } else if (expectedUnit === "gigabyte-hours") {
    if (!["gb-h", "gb-hour", "gb-hours", "gigabyte-hour", "gigabyte-hours"].includes(normalizedUnit)) {
      hold("dashboard_unit_invalid");
    }
    result = number;
  } else {
    const allowed = {
      minutes: ["minute", "minutes", "min"],
      invocations: ["invocation", "invocations"],
      deployments: ["deployment", "deployments", "build", "builds"],
      users: ["user", "users", "mau"],
      messages: ["message", "messages"],
      iops: ["iops", "operation", "operations"],
    }[expectedUnit];
    if (!allowed?.includes(normalizedUnit)) hold("dashboard_unit_invalid");
    result = number;
  }
  if (!Number.isSafeInteger(result)) hold("dashboard_counter_not_normalized_integer");
  return result;
}

function dateTokens(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) hold("dashboard_window_invalid");
  return new Set([
    value,
    new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date),
    new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date),
  ]);
}

function requireDashboardIdentity(snapshot, provider, publicProject) {
  const required = [expectedDashboardUrl(provider), publicProject];
  if (provider === "github") required.push("63amg0010-cpu");
  if (provider === "vercel-api" || provider === "vercel-web") {
    required.push("63amg0010-5358s-projects");
  }
  if (provider === "supabase") {
    required.push(identityValue("SUPABASE_ORG_ID"), identityValue("SUPABASE_PROJECT_ID"));
  }
  if (required.some((value) => typeof value !== "string" || !snapshot.includes(value))) {
    hold("dashboard_identity_missing");
  }
}

function billingMonthWindows(capturedAt) {
  const capture = new Date(capturedAt);
  if (!Number.isFinite(capture.getTime())) hold("captured_at_invalid");
  const horizon = new Date(capture.getTime() + HORIZON_MS);
  const windows = [];
  let start = new Date(Date.UTC(capture.getUTCFullYear(), capture.getUTCMonth(), 1));
  while (start < horizon) {
    const end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1));
    const windowStart = start.toISOString().replace(".000Z", "Z");
    const windowEnd = end.toISOString().replace(".000Z", "Z");
    windows.push({
      start: windowStart,
      end: windowEnd,
      id: `billing-month:${windowStart}:${windowEnd}`,
      current: start <= capture && capture < end,
    });
    start = end;
  }
  return windows;
}

function anchoredBillingMonthWindows(capturedAt, billingWindowStart, billingWindowEnd) {
  const capture = new Date(capturedAt);
  let start = new Date(billingWindowStart);
  let end = new Date(billingWindowEnd);
  if (
    !Number.isFinite(capture.getTime()) ||
    !Number.isFinite(start.getTime()) ||
    !Number.isFinite(end.getTime()) ||
    !(start <= capture && capture < end)
  ) {
    hold("dashboard_window_invalid");
  }
  const horizon = new Date(capture.getTime() + HORIZON_MS);
  const windows = [];
  while (start < horizon) {
    const windowStart = start.toISOString().replace(".000Z", "Z");
    const windowEnd = end.toISOString().replace(".000Z", "Z");
    windows.push({
      start: windowStart,
      end: windowEnd,
      id: `billing-month:${windowStart}:${windowEnd}`,
      current: start <= capture && capture < end,
    });
    start = end;
    end = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, end.getUTCDate()));
  }
  return windows;
}

function dashboardProjection(
  snapshot,
  provider,
  publicProject,
  capturedAt,
  billingWindowStart,
  billingWindowEnd,
) {
  if (
    typeof snapshot !== "string" ||
    snapshot.length === 0 ||
    snapshot.length > 2 * 1024 * 1024 ||
    snapshot.includes("PROVIDER_CAPTURE_JSON_BEGIN")
  ) {
    hold("dashboard_snapshot_invalid");
  }
  requireDashboardIdentity(snapshot, provider, publicProject);
  for (const boundary of [billingWindowStart, billingWindowEnd]) {
    if (![...dateTokens(boundary)].some((token) => snapshot.includes(token))) {
      hold("dashboard_window_missing");
    }
  }
  const planTokens = {
    github: ["Public repository", "public-standard"],
    "vercel-api": ["Hobby"],
    "vercel-web": ["Hobby"],
    supabase: ["Free"],
  }[provider];
  if (!planTokens.some((token) => snapshot.includes(token))) hold("dashboard_plan_missing");
  const disabledTokens = [
    "Paid usage disabled",
    "Overages disabled",
    "Spend limit: $0",
    "You won't be charged any extra for usage",
  ];
  if (!disabledTokens.some((token) => snapshot.includes(token))) hold("dashboard_billing_guard_missing");
  if (
    provider === "supabase" &&
    (!snapshot.includes("Spend cap") ||
      !snapshot.includes("You won't be charged any extra for usage"))
  ) {
    hold("dashboard_addon_guard_missing");
  }
  const dimensions = [];
  const quotaWindows =
    provider === "supabase"
      ? anchoredBillingMonthWindows(capturedAt, billingWindowStart, billingWindowEnd)
      : billingMonthWindows(capturedAt);
  for (const [name, contract] of Object.entries(DOM_DIMENSIONS[provider])) {
    const labels = contract.labels.map(escapeRegex).join("|");
    const pattern = new RegExp(
      `(?:${labels})[\\s\\S]{0,240}?([0-9][0-9,.]*)\\s*([A-Za-z-]+)?\\s*(?:/|of)\\s*([0-9][0-9,.]*)\\s*([A-Za-z-]+)`,
      "iu",
    );
    const match = pattern.exec(snapshot);
    if (!match) hold("dashboard_dimension_missing");
    const observedUnit = match[2] || match[4];
    const observed = normalizeDomMeasure(match[1], observedUnit, contract.unit);
    const quota = normalizeDomMeasure(match[3], match[4], contract.unit);
    if (quota <= 0) hold("quota_invalid");
    for (const window of quotaWindows) {
      dimensions.push({
        name,
        observed_usage: window.current ? observed : 0,
        quota,
        window_kind: "billing-month",
        window_start: window.start,
        window_end: window.end,
        window_id: window.id,
        status: "known",
        units_per_page_request: 1,
        ...(provider === "supabase" ? { unit: contract.unit } : {}),
      });
    }
  }
  return {
    provider,
    public_project: publicProject,
    plan: EXPECTED_PLANS[provider],
    paid_enabled: false,
    overage_enabled: false,
    quota_status: "known",
    source_url: expectedDashboardUrl(provider),
    identity_bindings: currentIdentityBindings(provider),
    dimensions,
    ...(provider === "supabase" ? { addon_enabled: false } : {}),
  };
}

function currentIdentityBindings(provider) {
  return IDENTITY_ENVS[provider].map((name) => {
    const value = identityValue(name);
    return { env: name, sha256: sha256Bytes(Buffer.from(value)) };
  });
}

function buildSupabasePolicyExclusions(spec, projection) {
  if (!Array.isArray(spec.policyEvidence) || spec.policyEvidence.length !== 3) {
    hold("supabase_policy_evidence_incomplete");
  }
  const evidenceByUrl = new Map();
  const captured = parseUtc(spec.capturedAt, "captured_at_invalid");
  for (const evidence of spec.policyEvidence) {
    exactKeys(
      evidence,
      new Set(["url", "retrievedAt", "snapshot"]),
      "supabase_policy_evidence_invalid",
    );
    const url = nonemptyString(evidence.url, "supabase_policy_evidence_invalid");
    const retrieved = parseUtc(evidence.retrievedAt, "supabase_policy_evidence_invalid");
    const snapshot = nonemptyString(evidence.snapshot, "supabase_policy_evidence_invalid");
    if (
      evidenceByUrl.has(url) ||
      ![...SUPABASE_EXCLUSIONS.values()].some((contract) => contract.url === url) ||
      !(retrieved <= captured && captured < retrieved + POLICY_MAX_AGE_MS)
    ) {
      hold("supabase_policy_evidence_invalid");
    }
    const normalized = snapshot.toLowerCase();
    if (
      (url.endsWith("/disk-iops") &&
        (!normalized.includes("provisioned iops") ||
          !(normalized.includes("opt in") || normalized.includes("only charged")))) ||
      (url.endsWith("/disk-throughput") &&
        (!normalized.includes("disk throughput") ||
          !(normalized.includes("opt in") || normalized.includes("no charges apply")))) ||
      (url.endsWith("/logs") &&
        (!normalized.includes("coming soon") ||
          !(normalized.includes("billing") || normalized.includes("enforcement"))))
    ) {
      hold("supabase_policy_state_changed");
    }
    evidenceByUrl.set(url, {
      retrievedAt: evidence.retrievedAt,
      sha256: sha256Bytes(Buffer.from(snapshot)),
    });
  }
  const accountStatus = {
    plan: projection.plan,
    paid_enabled: projection.paid_enabled,
    overage_enabled: projection.overage_enabled,
    addon_enabled: projection.addon_enabled,
  };
  const accountStatusSha256 = sha256Bytes(Buffer.from(canonicalize(accountStatus)));
  return [...SUPABASE_EXCLUSIONS.entries()].map(([name, contract]) => {
    const evidence = evidenceByUrl.get(contract.url);
    if (!evidence) hold("supabase_policy_evidence_incomplete");
    return {
      name,
      status: "not_applicable",
      reason_code: contract.reason,
      policy_url: contract.url,
      policy_sha256: evidence.sha256,
      retrieved_at: evidence.retrievedAt,
      account_status_sha256: accountStatusSha256,
    };
  });
}

export function buildSupabaseOfficialDocument(spec) {
  if (
    spec.connectorVerified !== true ||
    !Array.isArray(spec.connectorBindings) ||
    spec.connectorBindings.length !== 4 ||
    spec.connectorBindings.some(
      (binding) => typeof binding !== "string" || !/^[0-9a-f]{64}$/u.test(binding),
    )
  ) {
    hold("supabase_connector_binding_invalid");
  }
  const projection = dashboardProjection(
    spec.dashboardSnapshot,
    "supabase",
    spec.publicProject,
    spec.capturedAt,
    spec.billingWindowStart,
    spec.billingWindowEnd,
  );
  if (!Array.isArray(projection.dimensions)) hold("dashboard_dimensions_invalid");
  const nonApplicableDimensions = buildSupabasePolicyExclusions(spec, projection);
  const captured = parseUtc(spec.capturedAt, "captured_at_invalid");
  const dimensions = projection.dimensions
    .filter(
      (dimension) =>
        parseUtc(dimension.window_start, "window_invalid") <= captured &&
        captured < parseUtc(dimension.window_end, "window_invalid"),
    )
    .map((dimension) => ({
      name: dimension.name,
      value: uint(dimension.observed_usage, "supabase_official_dimension_invalid"),
      unit: nonemptyString(dimension.unit, "supabase_official_dimension_invalid"),
      quota: uint(dimension.quota, "supabase_official_dimension_invalid"),
    }));
  return {
    schema: OFFICIAL_SCHEMA,
    provider: "supabase",
    captured_at: spec.capturedAt,
    billing_window_start: spec.billingWindowStart,
    billing_window_end: spec.billingWindowEnd,
    identity_bindings: currentIdentityBindings("supabase"),
    official_payloads: [
      {
        kind: "supabase-dashboard",
        value: {
          plan: projection.plan,
          paid_enabled: projection.paid_enabled,
          overage_enabled: projection.overage_enabled,
          addon_enabled: projection.addon_enabled,
          project_filter: identityValue("SUPABASE_PROJECT_ID"),
          billing_window_start: spec.billingWindowStart,
          billing_window_end: spec.billingWindowEnd,
          source_url: projection.source_url,
          dimensions,
          non_applicable_dimensions: nonApplicableDimensions,
          connector_bindings: [...spec.connectorBindings],
        },
      },
    ],
  };
}

export function projectProviderCapture({
  provider,
  publicProject,
  officialPayloads,
  dashboardSnapshot,
  capturedAt,
  billingWindowStart,
  billingWindowEnd,
  trailing30dPageRequests,
  workloadManifest,
}) {
  if (!PROVIDERS.has(provider) || PUBLIC_PROJECTS[provider] !== publicProject) hold("provider_project_invalid");
  const official = validateOfficialDocument(officialPayloads, provider, IDENTITY_ENVS[provider]);
  const projection = dashboardProjection(
    dashboardSnapshot,
    provider,
    publicProject,
    capturedAt,
    billingWindowStart,
    billingWindowEnd,
  );
  const expectedProjectionFields = new Set([
    "provider",
    "public_project",
    "plan",
    "paid_enabled",
    "overage_enabled",
    "quota_status",
    "source_url",
    "identity_bindings",
    "dimensions",
  ]);
  if (provider === "supabase") expectedProjectionFields.add("addon_enabled");
  exactKeys(projection, expectedProjectionFields, "dashboard_projection_schema_open");
  if (
    projection.provider !== provider ||
    projection.public_project !== publicProject ||
    projection.plan !== EXPECTED_PLANS[provider] ||
    projection.paid_enabled !== false ||
    projection.overage_enabled !== false ||
    (provider === "supabase" && projection.addon_enabled !== false) ||
    projection.quota_status !== "known" ||
    projection.source_url !== expectedDashboardUrl(provider) ||
    canonicalize(projection.identity_bindings) !== canonicalize(officialPayloads.identity_bindings)
  ) {
    hold("dashboard_projection_identity_invalid");
  }
  if (!Array.isArray(projection.dimensions)) hold("dashboard_dimensions_invalid");
  const names = new Set(projection.dimensions.map((value) => value.name));
  const expected = EXPECTED_DIMENSIONS[provider];
  if (names.size !== expected.size || [...names].some((name) => !expected.has(name))) {
    hold("dashboard_dimension_set_invalid");
  }
  const dimensions = projection.dimensions.map((value) => {
    const expectedFields = new Set([
      "name",
      "observed_usage",
      "quota",
      "window_kind",
      "window_start",
      "window_end",
      "window_id",
      "status",
      "units_per_page_request",
    ]);
    if (provider === "supabase") expectedFields.add("unit");
    exactKeys(value, expectedFields, "dashboard_dimension_schema_open");
    if (value.status !== "known") hold("dashboard_dimension_unknown");
    const operands = structuredClone(workloadManifest);
    exactKeys(operands, OPERAND_FIELDS, "workload_manifest_invalid");
    operands.traffic = {
      trailing_30d_page_requests: uint(trailing30dPageRequests, "traffic_invalid"),
      units_per_page_request: uint(value.units_per_page_request, "traffic_invalid"),
    };
    const dimension = {
      name: value.name,
      window_id: value.window_id,
      observed_usage: uint(value.observed_usage, "usage_invalid"),
      added_usage_raw: 0,
      quota: uint(value.quota, "quota_invalid"),
      window_kind: value.window_kind,
      window_start: value.window_start,
      window_end: value.window_end,
      status: "known",
      projection_operands: operands,
    };
    if (dimension.quota <= 0) hold("quota_invalid");
    const windowStart = parseUtc(dimension.window_start, "window_invalid");
    const windowEnd = parseUtc(dimension.window_end, "window_invalid");
    const capture = parseUtc(capturedAt, "captured_at_invalid");
    if (!(windowStart < windowEnd)) hold("window_invalid");
    if (official.usage !== null && windowStart <= capture && capture < windowEnd) {
      const officialValue = official.usage.get(dimension.name);
      if (
        (officialValue === undefined && dimension.observed_usage !== 0) ||
        (officialValue !== undefined &&
          (!Number.isSafeInteger(officialValue) || dimension.observed_usage !== officialValue))
      ) {
        hold("official_dashboard_counter_mismatch");
      }
    }
    dimension.added_usage_raw = deriveAddedUsageRaw(dimension, capturedAt);
    if ((dimension.observed_usage + Math.ceil(1.25 * dimension.added_usage_raw)) / dimension.quota >= 0.7) {
      hold("quota_threshold_exceeded");
    }
    return dimension;
  });
  if (
    officialPayloads.captured_at !== capturedAt ||
    officialPayloads.billing_window_start !== billingWindowStart ||
    officialPayloads.billing_window_end !== billingWindowEnd
  ) {
    hold("official_window_binding_mismatch");
  }
  const observation = {
    schema:
      provider === "supabase"
        ? "free-tier.provider-observation.v2"
        : OBSERVATION_SCHEMA,
    provider,
    public_project: publicProject,
    captured_at: capturedAt,
    plan: projection.plan,
    paid_enabled: false,
    overage_enabled: false,
    quota_status: "known",
    dimensions,
    ...(provider === "supabase"
      ? { non_applicable_dimensions: official.exclusions }
      : {}),
    source_url_class: "official-provider-api-or-dashboard",
    source_url: projection.source_url,
  };
  const response = {
    schema:
      provider === "supabase"
        ? "free-tier.provider-private-response.v2"
        : RESPONSE_SCHEMA,
    provider,
    observation_sha256: sha256Bytes(Buffer.from(canonicalize(observation))),
    official_payloads: officialPayloads.official_payloads,
  };
  return { observation, response };
}

function screenshotBuffer(value) {
  if (Buffer.isBuffer(value)) return value;
  if (value instanceof Uint8Array) return Buffer.from(value);
  if (value && typeof value.data === "string") return Buffer.from(value.data, "base64");
  if (typeof value === "string" && value.startsWith("data:image/")) {
    return Buffer.from(value.slice(value.indexOf(",") + 1), "base64");
  }
  hold("screenshot_invalid");
}

function validatedPngScreenshot(value) {
  const bytes = screenshotBuffer(value);
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (bytes.length < 128 || bytes.length > 16 * 1024 * 1024 || !bytes.subarray(0, 8).equals(signature)) {
    hold("screenshot_invalid");
  }
  let offset = 8;
  let width = 0;
  let height = 0;
  let channels = 0;
  let sawHeader = false;
  let sawEnd = false;
  const compressed = [];
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length) hold("screenshot_invalid");
    const length = bytes.readUInt32BE(offset);
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    const dataStart = offset + 8;
    const dataEnd = dataStart + length;
    const chunkEnd = dataEnd + 4;
    if (length > 16 * 1024 * 1024 || chunkEnd > bytes.length) hold("screenshot_invalid");
    const data = bytes.subarray(dataStart, dataEnd);
    if (!sawHeader) {
      if (type !== "IHDR" || length !== 13) hold("screenshot_invalid");
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      const bitDepth = data[8];
      const colorType = data[9];
      channels = { 0: 1, 2: 3, 4: 2, 6: 4 }[colorType] ?? 0;
      if (
        width < 320 ||
        height < 200 ||
        width > 10_000 ||
        height > 10_000 ||
        bitDepth !== 8 ||
        channels === 0 ||
        data[10] !== 0 ||
        data[11] !== 0 ||
        data[12] !== 0
      ) {
        hold("screenshot_invalid");
      }
      sawHeader = true;
    } else if (type === "IHDR") {
      hold("screenshot_invalid");
    }
    if (type === "IDAT") compressed.push(data);
    if (type === "IEND") {
      if (length !== 0 || chunkEnd !== bytes.length) hold("screenshot_invalid");
      sawEnd = true;
    }
    offset = chunkEnd;
  }
  if (!sawHeader || !sawEnd || compressed.length === 0) hold("screenshot_invalid");
  let pixels;
  try {
    pixels = inflateSync(Buffer.concat(compressed), {
      maxOutputLength: height * (1 + width * channels),
    });
  } catch {
    hold("screenshot_invalid");
  }
  const rowLength = 1 + width * channels;
  if (pixels.length !== height * rowLength) hold("screenshot_invalid");
  for (let row = 0; row < height; row += 1) {
    if (pixels[row * rowLength] > 4) hold("screenshot_invalid");
  }
  return bytes;
}

async function authenticatedScreenshot(spec) {
  const supplied = validatedPngScreenshot(spec.screenshotBytes);
  if (
    !spec.tab ||
    typeof spec.tab.screenshot !== "function" ||
    !spec.tab.playwright ||
    typeof spec.tab.playwright.domSnapshot !== "function"
  ) {
    hold("screenshot_provenance_missing");
  }
  let fresh;
  try {
    const freshSnapshot = await spec.tab.playwright.domSnapshot();
    if (freshSnapshot !== spec.dashboardSnapshot) hold("dashboard_snapshot_provenance_invalid");
    fresh = validatedPngScreenshot(await spec.tab.screenshot({ fullPage: false }));
  } catch (error) {
    if (error?.code) throw error;
    hold("screenshot_provenance_failed");
  }
  if (sha256Bytes(supplied) !== sha256Bytes(fresh)) hold("screenshot_provenance_invalid");
  return supplied;
}

async function exclusivePrivateWrite(filePath, bytes, parentPath, parentIdentity) {
  const parentBefore = await fs.lstat(parentPath).catch(() => hold("private_root_changed"));
  if (!sameFile(parentBefore, parentIdentity)) hold("private_root_changed");
  const noFollow = constants.O_NOFOLLOW ?? 0;
  const handle = await fs.open(
    filePath,
    constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | noFollow,
    0o600,
  );
  let opened;
  try {
    try {
      opened = await handle.stat();
      if (!opened.isFile() || opened.nlink !== 1) hold("private_output_alias_detected");
      await handle.writeFile(bytes);
      await handle.sync();
    } finally {
      await handle.close();
    }
    await fs.chmod(filePath, 0o600);
    if (IS_WINDOWS) hardenWindowsAcl(filePath);
    const parentAfter = await fs.lstat(parentPath).catch(() => hold("private_root_changed"));
    const outputAfter = await fs.lstat(filePath).catch(() => hold("private_output_changed"));
    const realParentAfter = await fs.realpath(parentPath).catch(() => hold("private_root_changed"));
    if (
      !sameFile(parentAfter, parentIdentity) ||
      !sameFile(outputAfter, opened) ||
      path.resolve(realParentAfter) !== parentPath ||
      outputAfter.isSymbolicLink() ||
      outputAfter.nlink !== 1
    ) {
      hold("private_output_changed");
    }
    await requirePrivateRegularFile(filePath);
    return opened;
  } catch (error) {
    if (opened) await removeCreatedFile(filePath, parentPath, parentIdentity, opened);
    throw error;
  }
}

async function removeCreatedFile(filePath, parentPath, parentIdentity, fileIdentity) {
  try {
    const parentNow = await fs.lstat(parentPath);
    const fileNow = await fs.lstat(filePath);
    if (sameFile(parentNow, parentIdentity) && sameFile(fileNow, fileIdentity)) {
      await fs.unlink(filePath);
    }
  } catch {
    // Cleanup is best effort; never follow a replacement path.
  }
}

export async function projectAndPersistProviderCapture(spec) {
  const providerRoot = path.resolve(spec.privateRoot, spec.provider);
  const privateRoot = path.resolve(spec.privateRoot);
  if (providerRoot !== privateRoot && !providerRoot.startsWith(`${privateRoot}${path.sep}`)) {
    hold("private_root_escape");
  }
  const initialRootStats = await requirePrivateDirectory(providerRoot);
  return withPrivateDirectoryLock(providerRoot, async () => {
    const rootStats = await requirePrivateDirectory(providerRoot);
    if (!sameFile(initialRootStats, rootStats)) hold("private_root_changed");
    return projectAndPersistProviderCaptureLocked(spec, providerRoot, rootStats);
  });
}

async function projectAndPersistProviderCaptureLocked(spec, providerRoot, rootStats) {
  const officialPayloads =
    spec.provider === "supabase"
      ? buildSupabaseOfficialDocument(spec)
      : spec.officialPayloads;
  const { observation, response } = projectProviderCapture({
    ...spec,
    officialPayloads,
  });
  const outputs = [];
  if (spec.provider === "supabase") {
    outputs.push([
      "official-payloads.json",
      Buffer.from(canonicalize(officialPayloads)),
    ]);
  }
  const observationBytes = Buffer.from(canonicalize(observation));
  const responseBytes = Buffer.from(canonicalize(response));
  const screenshotBytes = await authenticatedScreenshot(spec);
  outputs.push(
    ["observation.json", observationBytes],
    ["response.json", responseBytes],
    ["screenshot.png", screenshotBytes],
  );
  const written = [];
  try {
    for (const [name, bytes] of outputs) {
      const output = path.join(providerRoot, name);
      const identity = await exclusivePrivateWrite(output, bytes, providerRoot, rootStats);
      written.push({ output, identity });
    }
    return {
      provider: spec.provider,
      observation_sha256: sha256Bytes(observationBytes),
      response_sha256: sha256Bytes(responseBytes),
      screenshot_sha256: sha256Bytes(screenshotBytes),
    };
  } catch (error) {
    await Promise.all(
      written.map(({ output, identity }) => removeCreatedFile(output, providerRoot, rootStats, identity)),
    );
    throw error;
  }
}

export const testContract = Object.freeze({
  canonicalize,
});
