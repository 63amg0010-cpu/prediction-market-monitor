# Source-scope register

**Checked at:** 2026-07-20 (Asia/Seoul)

This register defines which sources may be activated in Phase 0. Public visibility is not authorization. Every adapter is fail-closed unless the listed official evidence and operational prerequisites are present. No paid fallback is authorized.

| Source | Official evidence checked | Current scope / rate evidence | State and activation decision |
|---|---|---|---|
| Reddit | [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms), [Reddit Developer Terms](https://redditinc.com/policies/developer-terms), [official API documentation](https://www.reddit.com/dev/api/) | Data API access requires the provider-issued access information (for example an OAuth token), accurate identification information, and ongoing compliance. Reddit may set limits at its discretion and reserves the right to charge future fees; the current official terms do **not** guarantee a numeric free rate. | **OPEN only for registered/approved OAuth Data API use.** No scraping, HTML, JSON, or unauthenticated fallback. Keep disabled until app registration/approval, token, scope, and observed limits are recorded. |
| DCInside | [Official `www.dcinside.com/robots.txt`](https://www.dcinside.com/robots.txt) | Generic `User-agent: *` is allowed, while named AI crawlers (including GPTBot, ClaudeBot, Google-Extended, CCBot, Bytespider, PerplexityBot and others) are disallowed. | **BLOCKED pending written reviewed route.** Robots rules are crawler guidance, not authorization to monitor community content. Do not infer permission from the generic allow; adapter remains disabled until a written, reviewed route exists. |
| Naver Finance / community | [Official `finance.naver.com/robots.txt`](https://finance.naver.com/robots.txt), [NAVER automated-access policy](https://policy.naver.com/policy/disclaimer.html) | `User-agent: *` is disallowed, with narrow path allowances; community-board pagination is explicitly disallowed (`/item/board.naver?code=*&page=*`). NAVER's policy permits automation only when expressly allowed/approved, via an authorized API client, or within the allowed robots scope. | **BLOCKED.** The narrow robots allowances are insufficient for a community monitor. No general crawling or scraping fallback. |
| Toss | [Toss Payments developer portal](https://developers.tosspayments.com/) (checked; no applicable community-data authorization located) | No official Toss authorization, community-data API scope, or approved access route was found in the checked evidence. | **BLOCKED / OPEN evidence gap.** Do not fabricate an endpoint or authorization. |

## Exclusivity rule

The product requirement is **Toss-or-Naver exclusivity**. Because Toss has no verified authorization and Naver's verified robots/policy scope is insufficient, **neither source may activate** until its own official authorization evidence is added and reviewed. Do not substitute another provider or silently relax the exclusivity rule.

## Recheck and evidence gate

Provider terms, quotas, robots files, and dashboards can change. Recheck all linked official sources immediately before deployment. An adapter may activate only when the evidence state is `OPEN`, the authorization prerequisite is captured, and the implementation records request outcomes, rate-limit responses, and collection-window health. Missing or ambiguous proof remains `BLOCKED`.
