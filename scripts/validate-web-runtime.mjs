import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "::1"]);

function truthy(value) {
  return ["1", "true", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());
}

function publicHttpsRuntime(env) {
  const value = String(env.OPENCLASS_PUBLIC_ORIGIN ?? "").trim();
  if (!value) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !LOCAL_HOSTNAMES.has(url.hostname.toLowerCase());
  } catch {
    return false;
  }
}

function headerValue(manifest, key) {
  const normalizedKey = key.toLowerCase();
  for (const route of manifest?.headers ?? []) {
    for (const header of route?.headers ?? []) {
      if (String(header?.key ?? "").toLowerCase() === normalizedKey) {
        return String(header?.value ?? "");
      }
    }
  }
  return "";
}

function directoryContainsText(directory, needle) {
  if (!needle || !fs.existsSync(directory)) return false;
  const pending = [directory];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(entryPath);
      } else if (entry.isFile() && /\.(?:js|mjs|cjs|html)$/.test(entry.name)) {
        if (fs.readFileSync(entryPath, "utf8").includes(needle)) return true;
      }
    }
  }
  return false;
}

export function validateWebRuntime({ env = process.env, buildDirectory }) {
  if (
    !publicHttpsRuntime(env) ||
    truthy(env.OPENCLASS_LOCAL_RUNTIME) ||
    truthy(env.NEXT_PUBLIC_OPENCLASS_LOCAL_RUNTIME)
  ) {
    return [];
  }

  const errors = [];
  const manifestPath = path.join(buildDirectory, "routes-manifest.json");
  if (!fs.existsSync(manifestPath)) {
    return [`Production web build is missing ${manifestPath}`];
  }

  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch {
    return [`Production web build has an invalid ${manifestPath}`];
  }

  const csp = headerValue(manifest, "Content-Security-Policy");
  const hsts = headerValue(manifest, "Strict-Transport-Security");
  if (!csp) errors.push("Production web build is missing Content-Security-Policy");
  if (/https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?/i.test(csp)) {
    errors.push("Production web build contains local-only CSP sources");
  }
  if (!csp.includes("upgrade-insecure-requests")) {
    errors.push("Production web build is missing upgrade-insecure-requests");
  }
  if (!hsts) errors.push("Production web build is missing Strict-Transport-Security");

  if (truthy(env.OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED)) {
    const siteKey = String(env.NEXT_PUBLIC_CLOUDFLARE_TURNSTILE_SITE_KEY ?? "").trim();
    if (!siteKey) {
      errors.push("Turnstile is enabled but its public site key is missing");
    } else if (!directoryContainsText(path.join(buildDirectory, "static", "chunks"), siteKey)) {
      errors.push("Turnstile is enabled but its public site key is not embedded in the web build");
    }
  }

  return errors;
}

function run() {
  const appDirectory = process.cwd();
  const buildDirectory = path.resolve(
    appDirectory,
    process.env.OPENCLASS_NEXT_DIST_DIR || ".next",
  );
  const errors = validateWebRuntime({ buildDirectory });
  if (errors.length) {
    console.error(`Refusing to start an invalid public web build:\n- ${errors.join("\n- ")}`);
    process.exitCode = 1;
    return;
  }
  console.log("Public web build validation passed");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  run();
}
