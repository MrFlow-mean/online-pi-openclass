# Sub2API at `/apikey`

This adapter publishes the Sub2API UI and gateway at
`https://open-classes.com/apikey` without taking over OpenClass's existing
`/api/*` namespace.

## Provenance

- frontend source baseline: `Wei-Shaw/sub2api@5a6143097db142b72a6fc848c214e97214470bdd`
- runtime image: Sub2API `0.1.168`, commit `99c8e4bf7564823bafbab369acab6539e734c1bb`
- pinned runtime digest: `sha256:85d29bfc69fa7a314cd2a35420dbe2faa6251ccbb3c3d1d4c56c732270e87479`
- deployed frontend archive SHA-256: `d65da73966229dd2ecb6cb6e2a65adad493f6c6705f200a3d85665b383a1a5e7`

## Build the prefixed frontend

Apply `frontend-subpath.patch` to the pinned Sub2API source, then run:

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend exec vitest run \
  src/api/__tests__/client.spec.ts \
  src/components/layout/__tests__/siteLogoSanitization.spec.ts
pnpm --dir frontend exec vue-tsc -b
VITE_API_BASE_URL=/apikey/api/v1 \
  pnpm --dir frontend exec vite build --base=/apikey/
```

Copy `backend/internal/web/dist` to `/opt/sub2api-apikey/www/apikey`.

## Runtime and proxy

The backend binds only to `127.0.0.1:18080`. Install
`nginx-locations.conf` in the existing OpenClass HTTPS server block and run
`nginx -t` before reloading Nginx.

OpenClass model clients must use the public HTTPS gateway below. The loopback
binding is only the private Nginx-to-container hop and is not an application
model endpoint.

The production `.env` is intentionally excluded. Create it with mode `0600`
from `.env.example`, replacing each placeholder with an independent random
secret.

The deployment keeps OpenClass `/api/*` unchanged. Sub2API uses:

- UI: `/apikey/*`
- management API: `/apikey/api/v1/*`
- model gateways: `/apikey/v1/*` and `/apikey/v1beta/*`
- direct Codex gateway: `/apikey/backend-api/codex/*`
- Codex Live gateway and sideband: `/apikey/v1/live` and `/apikey/v1/live/*`

## Connect OpenClass text models

OpenClass uses its provider-neutral Responses API adapter for this gateway.
Keep the generated gateway key in a restricted server file rather than in the
repository or an inline environment variable:

```dotenv
OPENCLASS_CODEX_TEXT_PROXY_URL=https://open-classes.com/apikey/v1
OPENCLASS_CODEX_TEXT_PROXY_API_KEY_FILE=/etc/openclass/model-proxy-api-key
OPENCLASS_TEXT_MODEL_PROVIDERS=openai_codex
```

Codex Live uses the same public gateway namespace while retaining its
dedicated transport behind Nginx:

```dotenv
OPENCLASS_CODEX_REALTIME_PROXY_URL=https://open-classes.com/apikey/v1/live
OPENCLASS_CODEX_REALTIME_PROXY_API_KEY_FILE=/etc/openclass/model-proxy-api-key
```

The configured Sub2API OpenAI group must contain at least one active,
schedulable account. OpenClass exposes every model in its Codex text model
catalog through the same adapter; model selection is not limited to the
default model.
