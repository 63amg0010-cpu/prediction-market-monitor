# Provider budget proof

**Checked at:** 2026-07-22 (Asia/Seoul)

This is the Phase 0 budget and scheduling record. Quotas are dynamic: recheck the linked provider dashboards and plan pages immediately before deployment. No paid fallback is approved by this record.

## GitHub Actions (Free)

| Item | Official current allowance / behavior | Operational decision |
|---|---|---|
| Private repositories | GitHub Free includes **2,000 standard-runner minutes/month**, **500 MB Actions artifact storage** (storage is shared with Packages), and 10 GB cache storage. | Budget the private-repository collector to stay below 2,000 minutes and 500 MB artifacts. Stop before the allowance is exhausted; do not assume overage is free. |
| Public repositories | Standard GitHub-hosted runners are **free and unlimited** for public repositories (larger runners remain charged). | The required collector and 15-minute verifier cadence is eligible only when the repository is intentionally public, uses a standard runner, and contains no committed secrets. |
| Schedule floor | `on.schedule` supports a shortest interval of once every **5 minutes**. | A five-minute schedule is the fastest supported cadence. |
| Schedule reliability | GitHub documents scheduled workflows as best-effort; high-load periods can delay or drop scheduled runs, so there is no timing SLA. | A scheduled run is not proof that a collection window occurred. Record actual start/completion and fail closed on missing windows. |
| Current workflow budget | Collection has no schedule and consumes Actions time only after an authenticated manual request, with a 6-minute bound per request. Ninety-six independent verifier slots per day at a 3-minute timeout can use up to **8,928 minutes** in a 31-day month. | `.github/workflows/verify.yml` skips private scheduled jobs. Public standard runners are the approved free path; there is no paid fallback. |
| Private manual exception | A private scheduled verifier is ineligible and remains fail-closed. | After checking remaining included minutes, an operator may dispatch one verifier with `authorize_private_minutes=true`. The default is false, and the authorization applies to one run only. |

Official sources: [Actions billing and included quotas](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [included product usage table](https://docs.github.com/en/billing/reference/product-usage-included), [workflow syntax and `on.schedule`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), [scheduled-workflow event behavior](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule).

## Vercel Hobby

| Item | Official current allowance / behavior | Operational decision |
|---|---|---|
| Function execution | Hobby Functions default to **10 seconds**, configurable to a maximum of **60 seconds (1 minute)** in the Hobby plan documentation. | Any request that may exceed 60 seconds must not run as a Vercel Hobby Function. Recheck the current project compute mode before deployment because Vercel also documents Fluid Compute variants. |
| Hobby cron | Hobby cron jobs may run only **once per day** and may be invoked at any point within the specified hour. | Do not use Vercel Hobby cron for five-minute collection or for a three-hour collection window. |
| Three-hour collection | A single Hobby Function cannot run for three hours, and Hobby cron cannot provide the required cadence. | **BLOCKED / fail closed:** no three-hour collector on Vercel Hobby; no paid-plan assumption or paid fallback. |

Official sources: [Hobby plan limits](https://vercel.com/docs/plans/hobby), [Function duration limits](https://vercel.com/docs/functions/limitations), [Hobby cron restrictions and accuracy](https://vercel.com/docs/cron-jobs/manage-cron-jobs), [Vercel limits reference](https://vercel.com/docs/limits).

## Supabase Free

| Item | Official current allowance / behavior | Operational decision |
|---|---|---|
| Database | **500 MB database size per project**. Supabase separately describes 1 GB Free disk, with read-only mode triggered by the 500 MB database quota. | Keep the database below the internal soft cap below; writes must stop before the hard cap. |
| Storage | **1 GB** storage size. | Keep object storage below the internal soft cap below; stop uploads before the hard cap. |
| Egress | Free quota is **5 GB uncached** and **5 GB cached** in the current billing table. | Track cached and uncached egress separately. Apply the 70% soft stop and 80% hard stop to each bucket independently; do not spend one bucket's headroom to justify overrun in the other. |
| Projects / inactivity | Free plan grants **two free projects**; Free projects can be paused after one week of inactivity. | Use at most two active Free projects and schedule health activity/monitoring so an inactive project is detected and recovered. |
| Internal cap policy | Provider pages do not define our desired 70%/80% gates. | Apply project policy: alert/soft-stop at **70%** of each plan quota; hard-stop new collection/writes at **80%**. This is an internal fail-closed cap, not a claim about Supabase enforcement. |

Official sources: [Supabase pricing](https://supabase.com/pricing), [billing plan quotas](https://supabase.com/docs/guides/platform/billing-on-supabase), [Free project pausing](https://supabase.com/docs/guides/platform/free-project-pausing), [database-size behavior](https://supabase.com/docs/guides/platform/database-size), [Storage documentation](https://supabase.com/docs/guides/storage), [egress/bandwidth usage](https://supabase.com/docs/guides/platform/manage-your-usage/egress).

## Recheck gate

Before deployment, recheck the linked dynamic plan pages and account dashboards. If a quota, schedule guarantee, or plan behavior differs from this record, keep the affected adapter disabled and update this proof before activation.
