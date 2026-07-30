import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const SUPPORTED_SERVICE_TIERS = new Set(["priority"]);

export default function openClassPiRuntimeSettings(pi: ExtensionAPI) {
  pi.registerProvider("openai", {
    name: "OpenAI",
    baseUrl: process.env.OPENAI_BASE_URL?.trim() || "https://api.openai.com/v1",
    apiKey: "$OPENAI_API_KEY",
    api: "openai-responses",
    models: [
      {
        id: "gpt-5.4-mini",
        name: "GPT 5.4 Mini",
        reasoning: true,
        thinkingLevelMap: {
          off: "none",
          minimal: null,
          low: "low",
          medium: "medium",
          high: "high",
          xhigh: "xhigh",
          max: null,
        },
        input: ["text", "image"],
        cost: { input: 0.75, output: 4.5, cacheRead: 0.075, cacheWrite: 0 },
        contextWindow: 400_000,
        maxTokens: 128_000,
      },
      {
        id: "gpt-5.4",
        name: "GPT 5.4",
        reasoning: true,
        thinkingLevelMap: {
          off: "none",
          minimal: null,
          low: "low",
          medium: "medium",
          high: "high",
          xhigh: "xhigh",
          max: null,
        },
        input: ["text", "image"],
        cost: { input: 2.5, output: 15, cacheRead: 0.25, cacheWrite: 0 },
        contextWindow: 1_050_000,
        maxTokens: 128_000,
      },
      {
        id: "gpt-5.5",
        name: "GPT 5.5",
        reasoning: true,
        thinkingLevelMap: {
          off: "none",
          minimal: null,
          low: "low",
          medium: "medium",
          high: "high",
          xhigh: "xhigh",
          max: null,
        },
        input: ["text", "image"],
        cost: { input: 5, output: 30, cacheRead: 0.5, cacheWrite: 0 },
        contextWindow: 1_050_000,
        maxTokens: 128_000,
      },
    ],
  });

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
