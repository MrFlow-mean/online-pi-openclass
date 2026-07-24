import type {
  AIModelAccessMethod,
  AIModelCatalog,
  AIModelOption,
  AIModelSelection,
} from "@/types";

export type GoogleRealtimeAudioMessage = {
  setupComplete?: Record<string, unknown>;
  error?: {
    code?: number;
    message?: string;
    status?: string;
  };
  serverContent?: {
    modelTurn?: {
      parts?: Array<{
        inlineData?: {
          mimeType?: string;
          data?: string;
        };
        text?: string;
      }>;
    };
    inputTranscription?: {
      text?: string;
    };
    outputTranscription?: {
      text?: string;
    };
    turnComplete?: boolean;
    interrupted?: boolean;
  };
};

export const FALLBACK_MODEL_CATALOG: AIModelCatalog = {
  text: [
    {
      provider: "openai_codex",
      model: "gpt-5.6-sol",
      access_method: "chatgpt_subscription",
      label: "OpenAI Codex GPT-5.6-Sol",
      capability: "text",
      enabled: false,
      configured: false,
      default: true,
    },
  ],
  realtime: [
    {
      provider: "openai",
      model: "gpt-realtime-2.1",
      access_method: "platform_credits",
      label: "OpenAI GPT Realtime 2.1",
      capability: "realtime",
      enabled: false,
      configured: false,
      default: true,
      transport: "openai_webrtc",
    },
  ],
  defaults: {
    text: {
      provider: "openai_codex",
      model: "gpt-5.6-sol",
      access_method: "chatgpt_subscription",
    },
    realtime: {
      provider: "openai",
      model: "gpt-realtime-2.1",
      access_method: "platform_credits",
    },
  },
};

export const MODEL_ACCESS_METHODS: ReadonlyArray<{
  id: AIModelAccessMethod;
  label: string;
  shortLabel: string;
  description: string;
}> = [
  {
    id: "chatgpt_subscription",
    label: "ChatGPT 订阅额度",
    shortLabel: "ChatGPT 订阅",
    description: "通过已连接的 ChatGPT 账号调用 Codex 订阅模型。",
  },
  {
    id: "personal_api",
    label: "自有模型 API",
    shortLabel: "自有 API",
    description: "使用你自己的模型 API Key，由对应服务商直接计费。",
  },
  {
    id: "platform_credits",
    label: "OpenClass 平台 API",
    shortLabel: "平台 API",
    description: "通过 OpenClass 托管的模型 API 调用；接入账户服务后使用平台充值额度结算。",
  },
];

const DEFAULT_ACCESS_METHOD_BY_PROVIDER: Partial<
  Record<AIModelSelection["provider"], AIModelAccessMethod>
> = {
  openai_codex: "chatgpt_subscription",
  openai: "platform_credits",
  deepseek: "platform_credits",
};

export function modelAccessMethod(
  selection: Pick<AIModelSelection, "provider" | "access_method">
): AIModelAccessMethod {
  return selection.access_method ?? DEFAULT_ACCESS_METHOD_BY_PROVIDER[selection.provider] ?? "personal_api";
}

export function modelAccessMethodLabel(selection: AIModelSelection | AIModelOption): string {
  const method = modelAccessMethod(selection);
  return MODEL_ACCESS_METHODS.find((item) => item.id === method)?.label ?? method;
}

export const PROVIDER_LABELS: Record<AIModelSelection["provider"], string> = {
  openai: "OpenAI",
  openai_codex: "OpenAI Codex",
  anthropic: "Anthropic",
  google: "Google",
  deepseek: "DeepSeek",
  kimi: "Kimi",
  minimax: "MiniMax",
  openai_compatible: "OpenAI 兼容",
  anthropic_compatible: "Anthropic 兼容",
};

export const TEXT_MODEL_STORAGE_KEY = "blackboard-ai:selected-text-model";
export const REALTIME_MODEL_STORAGE_KEY = "blackboard-ai:selected-realtime-model";

const DISABLED_TEXT_MODEL_PROVIDERS = new Set<AIModelSelection["provider"]>();
const DISABLED_REALTIME_MODEL_PROVIDERS = new Set<AIModelSelection["provider"]>();

export function modelSelectionKey(selection: AIModelSelection): string {
  return `${modelAccessMethod(selection)}:${selection.provider}:${selection.model}`;
}

export function modelOptionKey(option: AIModelOption): string {
  return `${modelAccessMethod(option)}:${option.provider}:${option.model}`;
}

export function findModelOption(options: AIModelOption[], selection: AIModelSelection | null): AIModelOption | null {
  if (!selection) {
    return null;
  }
  return options.find((option) => modelOptionKey(option) === modelSelectionKey(selection)) ?? null;
}

function findEnabledModelOption(options: AIModelOption[], selection: AIModelSelection | null): AIModelOption | null {
  const option = findModelOption(options, selection);
  return option?.enabled ? option : null;
}

export function normalizeCourseStudioModelCatalog(catalog: AIModelCatalog): AIModelCatalog {
  return {
    ...catalog,
    text: catalog.text.map((option) =>
      DISABLED_TEXT_MODEL_PROVIDERS.has(option.provider)
        ? { ...option, enabled: false, configured: false, default: false }
        : option
    ),
    realtime: catalog.realtime.map((option) =>
      DISABLED_REALTIME_MODEL_PROVIDERS.has(option.provider)
        ? { ...option, enabled: false, configured: false, default: false }
        : option
    ),
  };
}

export function modelButtonLabel(option: AIModelOption | null, fallback: AIModelSelection | null): string {
  if (option) {
    return option.label;
  }
  if (!fallback) {
    return "未选择";
  }
  return `${PROVIDER_LABELS[fallback.provider]} ${fallback.model}`;
}

export function optionToSelection(option: AIModelOption): AIModelSelection {
  return selectionForModelOption(option, null);
}

export function selectionForModelOption(
  option: AIModelOption,
  current: AIModelSelection | null
): AIModelSelection {
  const reasoningOptions = option.supported_reasoning_efforts;
  const knownReasoningOptions = reasoningOptions ?? [];
  const supportedEfforts = new Set(knownReasoningOptions.map((item) => item.reasoning_effort));
  const currentEffort = current?.reasoning_effort?.trim() || null;
  const defaultEffort = option.default_reasoning_effort?.trim() || null;
  const reasoningEffort = reasoningOptions === undefined
    ? currentEffort ?? defaultEffort
    : supportedEfforts.size
      ? (currentEffort && supportedEfforts.has(currentEffort) ? currentEffort : null) ??
        (defaultEffort && supportedEfforts.has(defaultEffort) ? defaultEffort : null) ??
        knownReasoningOptions[0]?.reasoning_effort ??
        null
      : defaultEffort;

  const serviceTiers = option.service_tiers;
  const supportedServiceTiers = new Set((serviceTiers ?? []).map((item) => item.id));
  const currentHasServiceTier = Boolean(current && Object.hasOwn(current, "service_tier"));
  const currentServiceTier = current?.service_tier?.trim() || null;
  const defaultServiceTier = option.default_service_tier?.trim() || null;
  const serviceTier =
    serviceTiers === undefined
      ? currentHasServiceTier
        ? currentServiceTier
        : defaultServiceTier
      : currentHasServiceTier && currentServiceTier === null
        ? null
        : (currentServiceTier && supportedServiceTiers.has(currentServiceTier) ? currentServiceTier : null) ??
          (defaultServiceTier && supportedServiceTiers.has(defaultServiceTier) ? defaultServiceTier : null);

  return {
    provider: option.provider,
    model: option.model,
    access_method: option.access_method,
    reasoning_effort: reasoningEffort,
    service_tier: serviceTier,
  };
}

function isModelSelection(value: unknown): value is AIModelSelection {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<AIModelSelection>;
  return (
    typeof candidate.provider === "string" &&
    candidate.provider in PROVIDER_LABELS &&
    typeof candidate.model === "string" &&
    candidate.model.trim().length > 0 &&
    (candidate.access_method == null ||
      MODEL_ACCESS_METHODS.some((item) => item.id === candidate.access_method)) &&
    (candidate.reasoning_effort == null || typeof candidate.reasoning_effort === "string") &&
    (candidate.service_tier == null || typeof candidate.service_tier === "string")
  );
}

export function readStoredModelSelection(key: string): AIModelSelection | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as unknown;
    return isModelSelection(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export async function websocketMessageText(data: MessageEvent["data"]): Promise<string> {
  if (typeof data === "string") {
    return data;
  }
  if (data instanceof Blob) {
    return data.text();
  }
  if (data instanceof ArrayBuffer) {
    return new TextDecoder().decode(data);
  }
  if (ArrayBuffer.isView(data)) {
    return new TextDecoder().decode(data);
  }
  return String(data);
}

export function googleRealtimeErrorMessage(error: GoogleRealtimeAudioMessage["error"]): string {
  const rawMessage = error?.message?.trim() ?? "";
  const status = error?.status?.trim() ?? "";
  const lowerMessage = rawMessage.toLowerCase();
  const lowerStatus = status.toLowerCase();

  if (error?.code === 401 || lowerStatus.includes("unauthenticated")) {
    return "Google Gemini Live 认证失败。请检查统一模型 API Key 是否正确。";
  }
  if (error?.code === 403 || lowerStatus.includes("permission") || lowerMessage.includes("permission denied")) {
    return "Google Gemini Live 权限被拒绝。请检查 Google API Key 是否启用了 Gemini API，并确认该 key 可使用 Live API。";
  }
  if (error?.code === 429 || lowerStatus.includes("quota") || lowerMessage.includes("quota")) {
    return "Google Gemini Live 配额不足或请求过于频繁，请稍后重试或检查 Google API 配额。";
  }
  if (rawMessage) {
    return `Google Gemini Live 连接失败：${rawMessage}`;
  }
  return "Google Gemini Live 连接失败。";
}

export function realtimeConnectionErrorMessage(error: unknown, selection: AIModelSelection): string {
  const errorName = typeof error === "object" && error && "name" in error ? String(error.name) : "";
  const rawMessage = error instanceof Error ? error.message.trim() : "";
  const lowerMessage = rawMessage.toLowerCase();

  if (
    errorName === "NotAllowedError" ||
    errorName === "SecurityError" ||
    lowerMessage === "permission denied" ||
    lowerMessage.includes("permission dismissed")
  ) {
    return "麦克风权限被拒绝。请在浏览器地址栏允许本网站使用麦克风；如果通过本地启动页打开，请重新打开启动页或点“直接打开前端”；如果不是 localhost，请通过 HTTPS 打开页面。";
  }
  if (errorName === "NotFoundError" || lowerMessage.includes("requested device not found")) {
    return "没有找到可用麦克风。请连接或启用麦克风后重试。";
  }
  if (errorName === "NotReadableError" || lowerMessage.includes("could not start audio source")) {
    return "麦克风暂时不可用，可能正被其他应用占用。请关闭占用麦克风的应用后重试。";
  }
  if (rawMessage) {
    return rawMessage;
  }
  return `连接 ${PROVIDER_LABELS[selection.provider]} 实时语音失败`;
}

export function persistModelSelection(key: string, selection: AIModelSelection) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(selection));
}

export function resolveModelSelection(
  options: AIModelOption[],
  preferred: AIModelSelection | null,
  fallback: AIModelSelection
): AIModelSelection {
  const preferredOption = preferred ? findEnabledModelOption(options, preferred) : null;
  if (preferredOption) {
    return selectionForModelOption(preferredOption, preferred);
  }
  const fallbackOption = findEnabledModelOption(options, fallback);
  if (fallbackOption) {
    return selectionForModelOption(fallbackOption, fallback);
  }
  const defaultOption =
    options.find((option) => option.default && option.enabled) ?? options.find((option) => option.enabled) ?? options[0];
  return defaultOption ? optionToSelection(defaultOption) : fallback;
}
