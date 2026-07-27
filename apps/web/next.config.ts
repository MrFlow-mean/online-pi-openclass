import path from "node:path";

import type { NextConfig } from "next";

const isDevelopment = process.env.NODE_ENV === "development";

function configuredOriginSources() {
  const sources = new Set<string>();
  for (const value of [process.env.OPENCLASS_PUBLIC_ORIGIN, process.env.NEXT_PUBLIC_API_BASE_URL]) {
    if (!value) continue;
    try {
      const origin = new URL(value).origin;
      sources.add(origin);
      const websocketUrl = new URL(origin);
      websocketUrl.protocol = websocketUrl.protocol === "https:" ? "wss:" : "ws:";
      sources.add(websocketUrl.origin);
    } catch {
      // Invalid deployment input is ignored instead of weakening the policy.
    }
  }
  if (isDevelopment) {
    [
      "http://localhost:8000",
      "http://127.0.0.1:8000",
      "ws://localhost:8000",
      "ws://127.0.0.1:8000",
    ].forEach((source) => sources.add(source));
  }
  return [...sources];
}

const applicationSources = configuredOriginSources();
const paypalSources = [
  "https://www.paypal.com",
  "https://*.paypal.com",
  "https://www.paypalobjects.com",
  "https://*.paypalobjects.com",
  "https://*.venmo.com",
];
const cspDirectives = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDevelopment ? " 'unsafe-eval'" : ""} https://challenges.cloudflare.com ${paypalSources.join(" ")} https://applepay.cdn-apple.com https://pay.google.com`,
  `style-src 'self' 'unsafe-inline' ${paypalSources.join(" ")}`,
  `img-src 'self' data: blob: https://api.dicebear.com ${paypalSources.join(" ")} https://pay.google.com`,
  `font-src 'self' data: https://www.paypalobjects.com https://*.paypalobjects.com`,
  `connect-src 'self' ${applicationSources.join(" ")} https://challenges.cloudflare.com ${paypalSources.join(" ")} https://pay.google.com https://applepay.cdn-apple.com`,
  `frame-src 'self' https://challenges.cloudflare.com ${paypalSources.join(" ")} https://pay.google.com`,
  `child-src 'self' https://challenges.cloudflare.com ${paypalSources.join(" ")} https://pay.google.com`,
  "worker-src 'self' blob:",
  "media-src 'self' data: blob:",
  "object-src 'none'",
  "base-uri 'self'",
  `form-action 'self' https://www.paypal.com https://*.paypal.com`,
  "frame-ancestors 'none'",
  ...(!isDevelopment ? ["upgrade-insecure-requests"] : []),
];

const securityHeaders = [
  { key: "Content-Security-Policy", value: cspDirectives.join("; ") },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value:
      'camera=(), geolocation=(), microphone=(self), payment=(self "https://www.paypal.com" "https://pay.google.com"), publickey-credentials-get=(self)',
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin-allow-popups" },
  ...(!isDevelopment
    ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]
    : []),
];

const nextConfig: NextConfig = {
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  distDir: process.env.OPENCLASS_NEXT_DIST_DIR || ".next",
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "api.dicebear.com",
      },
    ],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
