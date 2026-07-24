# Web security deployment decision

The web app applies its browser security headers through `apps/web/next.config.js` to every
route, including Next static assets and route handlers.

- CSP permits only same-origin application resources, permits the inline scripts/styles currently
  required by Next, and disables plugins, hostile base URLs, and embedding. `unsafe-eval` is added
  only while `NODE_ENV=development`, because Next development tooling requires it; production CSP
  never adds it.
- `X-Content-Type-Options`, `Referrer-Policy`, and `Permissions-Policy` provide the baseline
  browser protections. HSTS is emitted only for a production Vercel deployment or an explicitly
  HTTPS `WEB_PUBLIC_ORIGIN`, so local HTTP development remains usable.
- Login rate-limit identity trusts `x-vercel-forwarded-for` only when Vercel has set `VERCEL=1`.
  Vercel documents that it supplies the requesting public IP and overwrites external
  `x-forwarded-for` values to prevent spoofing. The value must be one exact IPv4 or IPv6 address;
  malformed or multi-hop values, and every non-Vercel request, use the shared `unavailable` bucket.
  The value is forwarded only to the existing login API boundary and is not logged by the web app.

Before production deployment, request the deployed HTTPS login route and a static `/_next/` asset
and inspect their response headers. Confirm the Vercel project exposes `VERCEL=1` at runtime and
that upstream proxies have not changed the documented header contract.

Sources: [Next.js header configuration](https://nextjs.org/docs/app/api-reference/config/next-config-js/headers),
[Next.js CSP guidance](https://nextjs.org/docs/app/guides/content-security-policy), and
[Vercel request headers](https://vercel.com/docs/headers/request-headers).
