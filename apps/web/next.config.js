/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: { cpus: 1 },
  output: "standalone",
  async headers() {
    const isDevelopment = process.env.NODE_ENV === "development"
    const isHttpsProduction =
      process.env.NODE_ENV === "production" &&
      (process.env.VERCEL_ENV === "production" ||
        process.env.WEB_PUBLIC_ORIGIN?.startsWith("https://") === true)
    const contentSecurityPolicy = [
      "default-src 'self'",
      `script-src 'self' 'unsafe-inline'${isDevelopment ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' blob: data:",
      "font-src 'self'",
      "connect-src 'self'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "frame-ancestors 'none'",
    ].join("; ")
    const securityHeaders = [
      { key: "Content-Security-Policy", value: contentSecurityPolicy },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      {
        key: "Permissions-Policy",
        value: "camera=(), microphone=(), geolocation=(), browsing-topics=(), payment=(), usb=()",
      },
    ]
    if (isHttpsProduction) {
      securityHeaders.push({
        key: "Strict-Transport-Security",
        value: "max-age=31536000; includeSubDomains",
      })
    }
    return [{ source: "/(.*)", headers: securityHeaders }]
  },
}

module.exports = nextConfig
