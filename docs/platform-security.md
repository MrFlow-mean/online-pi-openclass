# Platform security deployment

OpenClass applies CSRF checks and security response headers in the API, and a payment-compatible CSP in Next.js. Login, registration, email-code, email-verification, and password-recovery routes enforce the shared Turnstile and rate-limit services.

## Cloudflare Turnstile

1. Create a Turnstile widget for every production hostname in the Cloudflare dashboard.
2. Build the web app with `NEXT_PUBLIC_CLOUDFLARE_TURNSTILE_SITE_KEY`.
3. Keep `OPENCLASS_CLOUDFLARE_TURNSTILE_SECRET_KEY` only in the backend runtime environment.
4. Set `OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED=true` and list exact hostnames in `OPENCLASS_CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAMES`.
5. Keep production keys out of logs, browser variables, repository files, and CI artifacts. Use Cloudflare's official dummy keys only in isolated automated-test environments.

The backend always calls Cloudflare's [Siteverify API](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/) and binds a successful response to the expected hostname and action. Missing configuration, upstream errors, malformed responses, and timeouts fail closed.

## Reverse proxy and client IP

`X-Forwarded-For` is ignored unless the immediate peer is within `OPENCLASS_TRUSTED_PROXY_CIDRS`. Add the local Nginx address and, when Cloudflare connects directly to Nginx, the current networks from Cloudflare's [published IP ranges](https://www.cloudflare.com/ips/). The proxy must append or replace forwarding headers consistently; do not trust a header copied unchanged from the public request.

Uvicorn's own `forwarded_allow_ips` setting runs before application middleware. Keep it restricted to the real local proxy addresses; never set it to `*` on a public listener, because application code cannot recover the original socket peer after Uvicorn has replaced it.

The bundled rate limiter is process-local. It is suitable for the current single API process. Before scaling to multiple replicas, keep the same service contract and replace storage with a shared atomic backend such as Valkey/Redis.

## Browser security policy

The web CSP permits only the app, configured API/WebSocket origins, Cloudflare Turnstile, PayPal/Card Fields, Apple Pay, Google Pay, and the existing DiceBear image source. PayPal popups require `Cross-Origin-Opener-Policy: same-origin-allow-popups`. Review browser console CSP reports during every payment-provider change before production rollout.

Do not enable HSTS until the public origin and all required subdomains are HTTPS-ready. `OPENCLASS_ENV=production` enables the API HSTS header; the production Next.js build emits it automatically.
