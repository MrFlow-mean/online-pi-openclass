import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const SUPPORTED_SERVICE_TIERS = new Set(["priority"]);

export default function openClassPiRuntimeSettings(pi: ExtensionAPI) {
  const serviceTier = process.env.OPENCLASS_PI_SERVICE_TIER?.trim() ?? "";
  if (!serviceTier) return;
  if (!SUPPORTED_SERVICE_TIERS.has(serviceTier)) {
    throw new Error("OpenClass Pi runtime received an unsupported service tier");
  }

  pi.on("before_provider_request", (event) => {
    if (!event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) {
      throw new Error("OpenClass Pi runtime received an invalid provider payload");
    }
    return { ...event.payload, service_tier: serviceTier };
  });
}
