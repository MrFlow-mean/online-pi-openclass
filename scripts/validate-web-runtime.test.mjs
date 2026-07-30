import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { validateWebRuntime } from "./validate-web-runtime.mjs";

function buildFixture({ csp, hsts = "", chunk = "" }) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "openclass-web-runtime-"));
  const chunks = path.join(directory, "static", "chunks");
  fs.mkdirSync(chunks, { recursive: true });
  fs.writeFileSync(
    path.join(directory, "routes-manifest.json"),
    JSON.stringify({
      headers: [
        {
          source: "/:path*",
          headers: [
            { key: "Content-Security-Policy", value: csp },
            ...(hsts ? [{ key: "Strict-Transport-Security", value: hsts }] : []),
          ],
        },
      ],
    }),
  );
  fs.writeFileSync(path.join(chunks, "app.js"), chunk);
  return directory;
}

test("local runtimes do not require a production build", () => {
  assert.deepEqual(
    validateWebRuntime({
      env: {
        OPENCLASS_PUBLIC_ORIGIN: "https://open-classes.com",
        OPENCLASS_LOCAL_RUNTIME: "true",
      },
      buildDirectory: "/missing",
    }),
    [],
  );
});

test("public runtimes reject local security headers and missing Turnstile configuration", () => {
  const buildDirectory = buildFixture({
    csp: "default-src 'self'; connect-src http://localhost:8000",
  });
  const errors = validateWebRuntime({
    env: {
      OPENCLASS_PUBLIC_ORIGIN: "https://open-classes.com",
      OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED: "true",
    },
    buildDirectory,
  });
  assert.ok(errors.includes("Production web build contains local-only CSP sources"));
  assert.ok(errors.includes("Production web build is missing upgrade-insecure-requests"));
  assert.ok(errors.includes("Production web build is missing Strict-Transport-Security"));
  assert.ok(errors.includes("Turnstile is enabled but its public site key is missing"));
});

test("public runtimes accept a production build with an embedded Turnstile site key", () => {
  const siteKey = "public-turnstile-site-key";
  const buildDirectory = buildFixture({
    csp:
      "default-src 'self'; script-src https://challenges.cloudflare.com; " +
      "upgrade-insecure-requests",
    hsts: "max-age=31536000; includeSubDomains",
    chunk: `const siteKey = ${JSON.stringify(siteKey)};`,
  });
  assert.deepEqual(
    validateWebRuntime({
      env: {
        OPENCLASS_PUBLIC_ORIGIN: "https://open-classes.com",
        OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED: "true",
        NEXT_PUBLIC_CLOUDFLARE_TURNSTILE_SITE_KEY: siteKey,
      },
      buildDirectory,
    }),
    [],
  );
});

test("public runtimes reject a site key that is absent from the web build", () => {
  const buildDirectory = buildFixture({
    csp: "default-src 'self'; upgrade-insecure-requests",
    hsts: "max-age=31536000; includeSubDomains",
  });
  assert.ok(
    validateWebRuntime({
      env: {
        OPENCLASS_PUBLIC_ORIGIN: "https://open-classes.com",
        OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED: "true",
        NEXT_PUBLIC_CLOUDFLARE_TURNSTILE_SITE_KEY: "not-embedded",
      },
      buildDirectory,
    }).includes("Turnstile is enabled but its public site key is not embedded in the web build"),
  );
});
