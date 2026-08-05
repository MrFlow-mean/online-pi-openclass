import type {
  AIModelCatalog,
  AIProvider,
  AIProviderCredentialStatus,
  AdminOverview,
  AuthProviderView,
  AuthSessionResponse,
  BatchLessonActionRequest,
  BoardTaskUpdateStreamPayload,
  ChatRequestPayload,
  ChatResponse,
  CoursePackage,
  CodexLoginStartResponse,
  CodexLoginStatusResponse,
  CodexProviderStatus,
  DocumentAIEditPayload,
  DocumentSaveDelta,
  DocumentSavePayload,
  EmailCodeRequestResponse,
  EmailRegistrationRequest,
  GoogleRealtimeSessionPayload,
  GoogleRealtimeSessionResponse,
  LessonMergeResolution,
  LessonWorkspaceDelta,
  LessonMergeSessionView,
  LessonContributionStatus,
  LessonContributionView,
  RealtimeConnectPayload,
  RealtimeConnectResponse,
  RealtimeToolCallPayload,
  RealtimeToolCallResponse,
  RealtimeEventLogPayload,
  RequirementUpdateStreamPayload,
  AIModelSelection,
  AccountActionResponse,
  PasswordResetRequest,
  GitHubConnectionView,
  GitHubRepositoryView,
  RepositoryMapView,
  SourceCatalogBatchView,
  SourceCatalogView,
  SourceIngestionRecord,
  SourceContentView,
  SourceStructureView,
  WorkspaceState,
  UserView,
} from "@/types";

const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
export const OPENCLASS_AUTH_TOKEN_STORAGE_KEY = "openclass.auth.token";
export const OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY = "openclass.guest.auth.token";
export const OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY = "openclass.connected-guest.auth.token";
let guestAuthToken: string | null = null;
let transientAuthToken: string | null = null;
let legacyAuthTokenRead = false;

function readCookie(name: string) {
  if (typeof document === "undefined") {
    return null;
  }
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix))
    ?.slice(prefix.length);
  if (!cookie) {
    return null;
  }
  try {
    return decodeURIComponent(cookie);
  } catch {
    return cookie;
  }
}

function clearCookie(name: string) {
  if (typeof document === "undefined") {
    return;
  }
  document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
}

function readSessionToken(name: string) {
  if (typeof window === "undefined") {
    return null;
  }
  return window.sessionStorage.getItem(name);
}

function storeSessionToken(name: string, token: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(name, token);
}

function clearSessionToken(name: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(name);
}

export function getApiBase() {
  if (configuredApiBase) {
    return configuredApiBase;
  }
  if (typeof window !== "undefined" && window.location.hostname) {
    if (window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1") {
      return window.location.origin;
    }
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export function readAuthToken() {
  if (typeof window === "undefined") {
    return null;
  }
  if (transientAuthToken) {
    return transientAuthToken;
  }
  if (legacyAuthTokenRead) {
    return null;
  }
  legacyAuthTokenRead = true;
  const legacyLocalToken = window.localStorage.getItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  const legacyCookieToken = readCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  transientAuthToken = legacyLocalToken || legacyCookieToken;
  window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  if (legacyCookieToken) {
    clearCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  }
  return transientAuthToken;
}

export function readGuestAuthToken() {
  if (guestAuthToken) {
    return guestAuthToken;
  }
  guestAuthToken =
    readSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY) ||
    (typeof window === "undefined" ? null : window.localStorage.getItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY)) ||
    readCookie(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  return guestAuthToken;
}

export function readEffectiveAuthToken() {
  return readAuthToken() || readGuestAuthToken();
}

export function storeAuthToken(token?: string | null) {
  if (typeof window === "undefined") {
    return;
  }
  transientAuthToken = token || null;
  legacyAuthTokenRead = true;
  guestAuthToken = null;
  clearSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  clearCookie(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
}

export function storeGuestAuthToken(token: string) {
  guestAuthToken = token;
  if (typeof window === "undefined") {
    return;
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY);
  clearCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  storeSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY, token);
  document.cookie = `${OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY}=${encodeURIComponent(token)}; Path=/; SameSite=Lax${secure}`;
}

export function persistConnectedGuestAuthToken() {
  const token = readGuestAuthToken();
  if (!token || typeof window === "undefined") {
    return;
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  window.localStorage.setItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY, token);
  storeSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY, token);
  document.cookie = `${OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY}=${encodeURIComponent(token)}; Path=/; Max-Age=2592000; SameSite=Lax${secure}`;
}

export function clearAuthToken() {
  transientAuthToken = null;
  legacyAuthTokenRead = true;
  guestAuthToken = null;
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY);
  clearSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  if (readCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY)) {
    clearCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  }
  clearCookie(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
}

function authHeaders(headers?: HeadersInit) {
  const nextHeaders = new Headers(headers);
  if (!nextHeaders.has("Authorization")) {
    const token = readEffectiveAuthToken();
    if (token) {
      nextHeaders.set("Authorization", `Bearer ${token}`);
    }
  }
  return nextHeaders;
}

export function getApiWebSocketUrl(pathOrUrl: string) {
  if (pathOrUrl.startsWith("ws://") || pathOrUrl.startsWith("wss://")) {
    return pathOrUrl;
  }

  const apiBase = getApiBase();
  const baseUrl = new URL(apiBase);
  baseUrl.protocol = baseUrl.protocol === "https:" ? "wss:" : "ws:";
  return new URL(pathOrUrl, baseUrl).toString();
}

const HTML_ERROR_DOCUMENT_PATTERN =
  /(?:<!doctype\s+html|<html\b|<head\b|<body\b|&lt;html\b|&lt;head\b|&lt;body\b)/i;
const MODEL_PROXY_FAILURE_PATTERN = /Codex platform proxy request failed/i;
const GATEWAY_FAILURE_PATTERN =
  /(?:502|503|504)\s+(?:Bad Gateway|Gateway Time-out|Service Unavailable)/i;

export function userFacingApiErrorMessage(message: string, fallback: string) {
  const normalized = message.trim();
  if (!normalized || HTML_ERROR_DOCUMENT_PATTERN.test(normalized)) {
    return fallback;
  }
  if (MODEL_PROXY_FAILURE_PATTERN.test(normalized)) {
    return "The model service connection failed, please try again later.";
  }
  if (GATEWAY_FAILURE_PATTERN.test(normalized)) {
    return fallback;
  }
  return normalized;
}

export async function responseErrorMessage(response: Response, fallback: string) {
  const text = await response.text();
  let message = text || fallback;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      message = parsed.detail;
    }
  } catch {
    // Non-JSON business errors remain visible unless they are HTML gateway pages.
  }
  return userFacingApiErrorMessage(message, fallback);
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && !(init?.body instanceof FormData) && !(init?.body instanceof Blob)) {
    headers.set("Content-Type", "application/json");
  }
  if (typeof window !== "undefined" && !headers.has("Authorization")) {
    const token = readEffectiveAuthToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    credentials: "include",
  });

  if (!response.ok) {
    const message = await responseErrorMessage(response, `Request failed with ${response.status}`);
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

type ChatStreamHandlers = {
  onPhase?: (label: string) => void;
  onAgentActivity?: (event: NonNullable<ChatResponse["agent_activity"]>[number]) => void;
  onChatDelta?: (delta: string) => void;
  onDocumentDelta?: (delta: string) => void;
  onRequirementUpdate?: (payload: RequirementUpdateStreamPayload) => void;
  onBoardTaskUpdate?: (payload: BoardTaskUpdateStreamPayload) => void;
  onFinal?: (response: ChatResponse) => void;
};

export type CreditWallet = {
  user_id: string;
  balance_credits: number;
  reserved_credits: number;
  available_credits: number;
  paypal_configured: boolean;
  model_access_status: "syncing" | "ready" | "blocked";
  currency: string;
  updated_at: string;
};

export type CreditPackage = {
  id: string;
  amount_cents: number;
  amount_usd: string;
  credits: number;
};

export type CreditWalletOverview = {
  wallet: CreditWallet;
  packages: CreditPackage[];
};

export type CreditTransaction = {
  entry_id: string;
  kind: string;
  delta_credits: number;
  balance_after: number;
  provider: string | null;
  model: string | null;
  upstream_cost_microusd: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type ModelRunLogEvent = {
  id: string;
  occurred_at: string;
  event_type: string;
  context: Record<string, unknown>;
  payload: Record<string, unknown>;
};

export type ModelRunHistoryResponse = {
  lesson_id: string;
  events: ModelRunLogEvent[];
  next_cursor: string | null;
  truncated: boolean;
  cursor_found?: boolean;
};

export type PayPalOrder = {
  order_id: string;
  approve_url: string | null;
};

export type PayPalPaymentMethod = "redirect" | "paypal" | "card" | "apple_pay" | "google_pay";

export type PayPalClientConfig = {
  client_id: string;
  client_token: string;
  currency: string;
  mode: "sandbox" | "live";
};

export type PayPalCapture = {
  order_id: string;
  status: string;
  credited: boolean;
  wallet: CreditWallet;
};

export type ChatStreamFailureKind = "http" | "sse" | "missing_final" | "aborted";

export class ChatStreamTransportError extends Error {
  kind: ChatStreamFailureKind;
  status?: number;

  constructor(message: string, kind: ChatStreamFailureKind, status?: number) {
    super(message);
    this.name = "ChatStreamTransportError";
    this.kind = kind;
    this.status = status;
  }
}

export function isMissingChatStreamFinalError(error: unknown) {
  return error instanceof ChatStreamTransportError && error.kind === "missing_final";
}

function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (!dataLines.length) {
    return null;
  }
  return { event, data: dataLines.join("\n") };
}

function handleChatStreamBlock(block: string, handlers: ChatStreamHandlers) {
  const parsed = parseSseBlock(block);
  if (!parsed) {
    return;
  }
  const payload = JSON.parse(parsed.data) as Record<string, unknown>;
  if (parsed.event === "heartbeat") {
    return;
  }
  if (parsed.event === "phase") {
    const label = typeof payload.label === "string" ? payload.label : "";
    if (label) {
      handlers.onPhase?.(label);
    }
    return;
  }
  if (parsed.event === "agent_activity") {
    handlers.onAgentActivity?.(payload as unknown as NonNullable<ChatResponse["agent_activity"]>[number]);
    return;
  }
  if (parsed.event === "chat_delta") {
    const delta = typeof payload.delta === "string" ? payload.delta : "";
    if (delta) {
      handlers.onChatDelta?.(delta);
    }
    return;
  }
  if (parsed.event === "document_delta") {
    const delta = typeof payload.delta === "string" ? payload.delta : "";
    if (delta) {
      handlers.onDocumentDelta?.(delta);
    }
    return;
  }
  if (parsed.event === "requirement_update") {
    handlers.onRequirementUpdate?.(payload as unknown as RequirementUpdateStreamPayload);
    return;
  }
  if (parsed.event === "board_task_update") {
    handlers.onBoardTaskUpdate?.(payload as unknown as BoardTaskUpdateStreamPayload);
    return;
  }
  if (parsed.event === "final") {
    handlers.onFinal?.(payload as unknown as ChatResponse);
    return;
  }
  if (parsed.event === "error") {
    const message = userFacingApiErrorMessage(
      typeof payload.message === "string" ? payload.message : "",
      "Chat failed"
    );
    throw new ChatStreamTransportError(message, "sse");
  }
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

async function streamRequest(
  path: string,
  payload: unknown,
  handlers: ChatStreamHandlers,
  options?: { signal?: AbortSignal }
): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch(`${getApiBase()}${path}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      cache: "no-store",
      credentials: "include",
      signal: options?.signal,
    });
  } catch (fetchError) {
    if (isAbortError(fetchError) || options?.signal?.aborted) {
      throw new ChatStreamTransportError("Chat flow has stopped", "aborted");
    }
    const message = fetchError instanceof Error ? fetchError.message : "Chat stream connection failed";
    throw new ChatStreamTransportError(message, "missing_final");
  }
  if (!response.ok || !response.body) {
    const fallback =
      response.status >= 500
        ? `Could not connect to the chat service (HTTP ${response.status}). Please retry later.`
        : `Chat request failed (HTTP ${response.status}).`;
    const message = await responseErrorMessage(response, fallback);
    throw new ChatStreamTransportError(message, "http", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: ChatResponse | null = null;
  const streamHandlers: ChatStreamHandlers = {
    ...handlers,
    onFinal(responsePayload) {
      finalResponse = responsePayload;
      handlers.onFinal?.(responsePayload);
    },
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (block) {
          handleChatStreamBlock(block, streamHandlers);
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) {
        break;
      }
    }
  } catch (streamError) {
    if (streamError instanceof ChatStreamTransportError) {
      throw streamError;
    }
    if (isAbortError(streamError) || options?.signal?.aborted) {
      throw new ChatStreamTransportError("Chat flow has stopped", "aborted");
    }
    const message = streamError instanceof Error ? streamError.message : "Chat stream connection interrupted";
    throw new ChatStreamTransportError(message, "missing_final");
  }
  const rest = buffer.trim();
  if (rest) {
    handleChatStreamBlock(rest, streamHandlers);
  }
  if (!finalResponse) {
    throw new ChatStreamTransportError("Chat flow does not return final results", "missing_final");
  }
  return finalResponse;
}

export const api = {
  registerEmail(payload: EmailRegistrationRequest) {
    return request<AuthSessionResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ ...payload, guest_token: readGuestAuthToken() }),
    });
  },
  login(identifier: string, password: string, turnstileToken?: string | null) {
    return request<AuthSessionResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier, password, guest_token: readGuestAuthToken(), ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) }),
    });
  },
  requestEmailCode(email: string, turnstileToken?: string | null) {
    return request<EmailCodeRequestResponse>("/api/auth/email/code", {
      method: "POST",
      body: JSON.stringify({ email, ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) }),
    });
  },
  requestRegistrationEmailCode(email: string, turnstileToken?: string | null) {
    return request<EmailCodeRequestResponse>("/api/auth/register/email/code", {
      method: "POST",
      body: JSON.stringify({ email, ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) }),
    });
  },
  verifyEmailCode(challengeId: string, code: string, turnstileToken?: string | null) {
    return request<AuthSessionResponse>("/api/auth/email/verify", {
      method: "POST",
      body: JSON.stringify({
        challenge_id: challengeId,
        code,
        guest_token: readGuestAuthToken(),
        ...(turnstileToken ? { turnstile_token: turnstileToken } : {}),
      }),
    });
  },
  requestPasswordReset(email: string, turnstileToken?: string | null) {
    return request<EmailCodeRequestResponse>("/api/auth/password/forgot", {
      method: "POST",
      body: JSON.stringify({ email, turnstile_token: turnstileToken }),
    });
  },
  resetPassword(payload: PasswordResetRequest) {
    return request<AccountActionResponse>("/api/auth/password/reset", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  requestEmailVerification(turnstileToken?: string | null) {
    return request<EmailCodeRequestResponse>("/api/auth/email/verification/request", {
      method: "POST",
      body: JSON.stringify({ turnstile_token: turnstileToken }),
    });
  },
  confirmEmailVerification(challengeId: string, code: string, turnstileToken?: string | null) {
    return request<UserView>("/api/auth/email/verification/confirm", {
      method: "POST",
      body: JSON.stringify({ challenge_id: challengeId, code, turnstile_token: turnstileToken }),
    });
  },
  changePassword(currentPassword: string, newPassword: string, newPasswordConfirmation: string) {
    return request<AuthSessionResponse>("/api/auth/password/change", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
        new_password_confirmation: newPasswordConfirmation,
      }),
    });
  },
  logout() {
    return request<AccountActionResponse>("/api/auth/logout", { method: "POST" });
  },
  revokeAllSessions() {
    return request<AccountActionResponse>("/api/auth/sessions/revoke-all", { method: "POST" });
  },
  deleteAccount(password: string) {
    return request<AccountActionResponse>("/api/auth/account", {
      method: "DELETE",
      body: JSON.stringify({ password, confirmation: "DELETE" }),
    });
  },
  async exportAccountData() {
    const response = await fetch(`${getApiBase()}/api/auth/export`, {
      method: "GET",
      headers: authHeaders(),
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, `Export failed with ${response.status}`));
    }
    return response.blob();
  },
  startGuestSession() {
    return request<AuthSessionResponse>("/api/auth/guest", {
      method: "POST",
    });
  },
  getCurrentUser() {
    return request<UserView>("/api/auth/me");
  },
  getCreditWallet() {
    return request<CreditWalletOverview>("/api/billing/wallet");
  },
  getCreditTransactions() {
    return request<CreditTransaction[]>("/api/billing/transactions");
  },
  getPayPalClientConfig() {
    return request<PayPalClientConfig>("/api/billing/paypal/client-config");
  },
  createPayPalOrder(packageId: string, paymentMethod: PayPalPaymentMethod = "redirect") {
    return request<PayPalOrder>("/api/billing/paypal/orders", {
      method: "POST",
      body: JSON.stringify({ package_id: packageId, payment_method: paymentMethod }),
    });
  },
  capturePayPalOrder(orderId: string) {
    return request<PayPalCapture>(
      `/api/billing/paypal/orders/${encodeURIComponent(orderId)}/capture`,
      { method: "POST" }
    );
  },
  getAuthProviders() {
    return request<AuthProviderView[]>("/api/auth/providers");
  },
  getAdminOverview() {
    return request<AdminOverview>("/api/admin/overview");
  },
  getAIModels() {
    return request<AIModelCatalog>("/api/ai-models");
  },
  getModelCredentials() {
    return request<AIProviderCredentialStatus[]>("/api/model-credentials");
  },
  saveModelCredential(provider: AIProvider, apiKey: string) {
    return request<AIProviderCredentialStatus>(
      `/api/model-credentials/${encodeURIComponent(provider)}`,
      {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      }
    );
  },
  deleteModelCredential(provider: AIProvider) {
    return request<AIProviderCredentialStatus>(
      `/api/model-credentials/${encodeURIComponent(provider)}`,
      { method: "DELETE" }
    );
  },
  getCodexStatus(includeRateLimits = false) {
    const query = includeRateLimits ? "?include_rate_limits=true" : "";
    return request<CodexProviderStatus>(`/api/codex/status${query}`);
  },
  startCodexDeviceLogin() {
    return request<CodexLoginStartResponse>("/api/codex/login/device", {
      method: "POST",
    });
  },
  startChatGPTPlatformLogin() {
    return request<CodexLoginStartResponse>("/api/codex/platform-login/device", {
      method: "POST",
    });
  },
  getCodexLoginStatus(loginId: string) {
    return request<CodexLoginStatusResponse>(`/api/codex/login/${encodeURIComponent(loginId)}`);
  },
  completeCodexPlatformLogin(loginId?: string) {
    const query = loginId ? `?login_id=${encodeURIComponent(loginId)}` : "";
    return request<AuthSessionResponse>(`/api/codex/login/complete${query}`, {
      method: "POST",
    });
  },
  cancelCodexLogin(loginId: string) {
    return request<CodexLoginStatusResponse>(`/api/codex/login/${encodeURIComponent(loginId)}/cancel`, {
      method: "POST",
    });
  },
  logoutCodex() {
    return request<{ ok: boolean }>("/api/codex/logout", {
      method: "POST",
    });
  },
  getWorkspace() {
    return request<WorkspaceState>("/api/workspace");
  },
  getModelRunHistory(lessonId: string, options?: { limit?: number; after?: string | null }) {
    const query = new URLSearchParams();
    if (options?.limit) {
      query.set("limit", String(options.limit));
    }
    if (options?.after) {
      query.set("after", options.after);
    }
    const suffix = query.size ? `?${query.toString()}` : "";
    return request<ModelRunHistoryResponse>(
      `/api/lessons/${encodeURIComponent(lessonId)}/model-run-history${suffix}`
    );
  },
  createPackage(title: string, summary = "") {
    return request<WorkspaceState>("/api/packages", {
      method: "POST",
      body: JSON.stringify({
        title,
        summary,
      }),
    });
  },
  openPackage(packageId: string) {
    return request<WorkspaceState>(`/api/packages/${packageId}/open`, {
      method: "POST",
    });
  },
  renamePackage(packageId: string, title: string) {
    return request<WorkspaceState>(`/api/packages/${packageId}`, {
      method: "POST",
      body: JSON.stringify({
        title,
      }),
    });
  },
  deletePackage(packageId: string) {
    return request<WorkspaceState>(`/api/packages/${packageId}/delete`, {
      method: "POST",
    });
  },
  moveLesson(lessonId: string, targetPackageId: string) {
    return request<WorkspaceState>(`/api/lessons/${lessonId}/move`, {
      method: "POST",
      body: JSON.stringify({
        target_package_id: targetPackageId,
      }),
    });
  },
  renameLesson(lessonId: string, title: string) {
    return request<WorkspaceState>(`/api/lessons/${lessonId}/rename`, {
      method: "POST",
      body: JSON.stringify({
        title,
      }),
    });
  },
  deleteLesson(lessonId: string) {
    return request<LessonWorkspaceDelta>(`/api/lessons/${lessonId}/delete?response_mode=delta`, {
      method: "POST",
    });
  },
  batchLessons(payload: BatchLessonActionRequest) {
    return request<WorkspaceState>("/api/lessons/batch", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getCoursePackage() {
    return request<CoursePackage>("/api/course-package");
  },
  listPackageSources(packageId: string) {
    return request<SourceIngestionRecord[]>(`/api/packages/${packageId}/sources`);
  },
  async deletePackageSource(packageId: string, sourceId: string) {
    const response = await fetch(`${getApiBase()}/api/packages/${packageId}/sources/${sourceId}`, {
      method: "DELETE",
      headers: authHeaders(),
      cache: "no-store",
      credentials: "include",
    });
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, `Source removal failed with ${response.status}`));
    }
    return response.json() as Promise<SourceIngestionRecord>;
  },
  renamePackageSource(packageId: string, sourceId: string, title: string) {
    return request<SourceIngestionRecord>(`/api/packages/${packageId}/sources/${sourceId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },
  retryPackageSource(packageId: string, sourceId: string) {
    return request<SourceIngestionRecord>(`/api/packages/${packageId}/sources/${sourceId}/retry`, {
      method: "POST",
    });
  },
  getPackageSourceContent(packageId: string, sourceId: string) {
    return request<SourceContentView>(`/api/packages/${packageId}/sources/${sourceId}/content`);
  },
  updatePackageSourceContent(packageId: string, sourceId: string, content: string) {
    return request<SourceContentView>(`/api/packages/${packageId}/sources/${sourceId}/content`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    });
  },
  async downloadPackageSource(packageId: string, sourceId: string) {
    const response = await fetch(`${getApiBase()}/api/packages/${packageId}/sources/${sourceId}/download`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, "Data download failed"));
    }
    return response.blob();
  },
  async getBoardAssetContent(assetId: string, options: { signal?: AbortSignal } = {}) {
    const response = await fetch(
      `${getApiBase()}/api/board-assets/${encodeURIComponent(assetId)}/content`,
      {
        headers: authHeaders(),
        cache: "no-store",
        credentials: "include",
        signal: options.signal,
      }
    );
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, "Failed to read board picture"));
    }
    return response.blob();
  },
  getPackageSourceStructure(packageId: string, sourceId: string) {
    return request<SourceStructureView>(`/api/packages/${packageId}/sources/${sourceId}/structure`);
  },
  rebuildPackageSourceStructure(packageId: string, sourceId: string) {
    return request<SourceStructureView>(`/api/packages/${packageId}/sources/${sourceId}/structure/rebuild`, {
      method: "POST",
    });
  },
  getPackageSourceCatalogs(packageId: string) {
    return request<SourceCatalogBatchView>(`/api/packages/${packageId}/sources/catalogs`);
  },
  getPackageSourceCatalog(packageId: string, sourceId: string) {
    return request<SourceCatalogView>(`/api/packages/${packageId}/sources/${sourceId}/catalog`);
  },
  rebuildPackageSourceCatalog(
    packageId: string,
    sourceId: string,
    catalogModel: AIModelSelection | null = null
  ) {
    const formData = new FormData();
    if (catalogModel) {
      formData.append("catalog_model", JSON.stringify(catalogModel));
    }
    return request<SourceCatalogView>(`/api/packages/${packageId}/sources/${sourceId}/catalog/rebuild`, {
      method: "POST",
      body: formData,
    });
  },
  refinePackageSourceCatalog(packageId: string, sourceId: string) {
    return request<SourceCatalogView>(`/api/packages/${packageId}/sources/${sourceId}/catalog/refine`, {
      method: "POST",
    });
  },
  pausePackageSourceCatalog(packageId: string, sourceId: string) {
    return request<SourceCatalogView>(`/api/packages/${packageId}/sources/${sourceId}/catalog/pause`, {
      method: "POST",
    });
  },
  getGitHubConnectionStatus() {
    return request<GitHubConnectionView>("/api/integrations/github/status");
  },
  startGitHubInstall(nextPath = "/studio") {
    return request<{ install_url: string }>("/api/integrations/github/install/start", {
      method: "POST",
      body: JSON.stringify({ next_path: nextPath }),
    });
  },
  listGitHubRepositories() {
    return request<GitHubRepositoryView[]>("/api/integrations/github/repositories");
  },
  disconnectGitHub() {
    return request<GitHubConnectionView>("/api/integrations/github/connection", {
      method: "DELETE",
    });
  },
  getRepositoryMap(packageId: string, sourceId: string) {
    return request<RepositoryMapView>(
      `/api/packages/${packageId}/sources/${sourceId}/repository-map`
    );
  },
  refreshRepositorySource(packageId: string, sourceId: string) {
    return request<SourceIngestionRecord>(
      `/api/packages/${packageId}/sources/${sourceId}/repository-refresh`,
      { method: "POST" }
    );
  },
  async importPackageSource(
    packageId: string,
    payload: {
      file?: File | null;
      sourceUri?: string;
      text?: string;
      title?: string;
      catalogModel?: AIModelSelection | null;
      learningGoal?: string;
    },
    options: { onUploadProgress?: (progress: number) => void } = {}
  ) {
    const formData = new FormData();
    if (payload.file) {
      formData.append("file", payload.file);
    }
    if (payload.sourceUri) {
      formData.append("source_uri", payload.sourceUri);
    }
    if (payload.text) {
      formData.append("text", payload.text);
    }
    if (payload.title) {
      formData.append("title", payload.title);
    }
    if (payload.catalogModel) {
      formData.append("catalog_model", JSON.stringify(payload.catalogModel));
    }
    if (payload.learningGoal) {
      formData.append("learning_goal", payload.learningGoal);
    }
    if (payload.file && typeof XMLHttpRequest !== "undefined") {
      return new Promise<SourceIngestionRecord>((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", `${getApiBase()}/api/packages/${packageId}/sources`);
        request.withCredentials = true;
        authHeaders().forEach((value, key) => request.setRequestHeader(key, value));
        request.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable) {
            options.onUploadProgress?.(Math.round((event.loaded / event.total) * 100));
          }
        });
        request.addEventListener("load", () => {
          if (request.status < 200 || request.status >= 300) {
            let message = request.responseText || `Source import failed with ${request.status}`;
            try {
              const parsed = JSON.parse(request.responseText) as { detail?: unknown };
              if (typeof parsed.detail === "string") {
                message = parsed.detail;
              }
            } catch {
              // Keep the raw response text for non-JSON errors.
            }
            reject(new Error(message));
            return;
          }
          try {
            options.onUploadProgress?.(100);
            resolve(JSON.parse(request.responseText) as SourceIngestionRecord);
          } catch {
            reject(new Error("The data was uploaded successfully, but the server returned an invalid status."));
          }
        });
        request.addEventListener("error", () => reject(new Error("Source upload failed. Check your connection and try again.")));
        request.addEventListener("abort", () => reject(new Error("Source upload was canceled.")));
        request.send(formData);
      });
    }
    const response = await fetch(`${getApiBase()}/api/packages/${packageId}/sources`, {
      method: "POST",
      body: formData,
      headers: authHeaders(),
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) {
      const message = await responseErrorMessage(response, `Source import failed with ${response.status}`);
      throw new Error(message);
    }
    return response.json() as Promise<SourceIngestionRecord>;
  },
  generateLesson(
    options: {
      branchFromLessonId?: string | null;
      startBlank?: boolean;
      targetPackageId?: string | null;
    } = {}
  ) {
    return request<LessonWorkspaceDelta>("/api/lessons/generate?response_mode=delta", {
      method: "POST",
      body: JSON.stringify({
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        branch_from_lesson_id: options.branchFromLessonId ?? null,
        target_package_id: options.targetPackageId ?? null,
        start_blank: options.startBlank ?? false,
      }),
    });
  },
  saveDocument(lessonId: string, payload: DocumentSavePayload) {
    return request<DocumentSaveDelta>(`/api/lessons/${lessonId}/document/save?response_mode=delta`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  saveDocumentBeacon(lessonId: string, payload: DocumentSavePayload) {
    if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
      return false;
    }
    const blob = new Blob([JSON.stringify(payload)], { type: "text/plain;charset=UTF-8" });
    return navigator.sendBeacon(
      `${getApiBase()}/api/lessons/${lessonId}/document/save-beacon?response_mode=delta`,
      blob
    );
  },
  saveDocumentKeepalive(lessonId: string, payload: DocumentSavePayload) {
    return fetch(`${getApiBase()}/api/lessons/${lessonId}/document/save?response_mode=delta`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      cache: "no-store",
      credentials: "include",
      keepalive: true,
    });
  },
  aiEditDocument(lessonId: string, payload: DocumentAIEditPayload) {
    return request<ChatResponse>(`/api/lessons/${lessonId}/document/ai-edit`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  async importDocx(lessonId: string, file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${getApiBase()}/api/lessons/${lessonId}/document/import-docx`, {
      method: "POST",
      body: formData,
      headers: authHeaders(),
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Upload failed with ${response.status}`);
    }
    return response.json() as Promise<CoursePackage>;
  },
  async exportDocx(lessonId: string) {
    const response = await fetch(`${getApiBase()}/api/lessons/${lessonId}/document/export-docx`, {
      headers: authHeaders(),
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Export failed with ${response.status}`);
    }
    return response.blob();
  },
  async exportHtml(lessonId: string) {
    const response = await fetch(`${getApiBase()}/api/lessons/${lessonId}/document/export-html`, {
      headers: authHeaders(),
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Export failed with ${response.status}`);
    }
    return response.blob();
  },
  async exportRidoc(lessonId: string) {
    const response = await fetch(
      `${getApiBase()}/api/lessons/${lessonId}/document/export-ridoc?source_mode=evidence`,
      {
        headers: authHeaders(),
        cache: "no-store",
        credentials: "include",
      }
    );
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, `RIDOC export failed with ${response.status}`));
    }
    return response.blob();
  },
  async importRidoc(file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${getApiBase()}/api/workspace/import-ridoc`, {
      method: "POST",
      body: formData,
      headers: authHeaders(),
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, `RIDOC import failed with ${response.status}`));
    }
    return response.json() as Promise<CoursePackage>;
  },
  createBranch(lessonId: string, name: string, fromCommitId?: string | null) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/branches`, {
      method: "POST",
      body: JSON.stringify({ name, from_commit_id: fromCommitId ?? null }),
    });
  },
  switchBranch(lessonId: string, name: string) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/branches/checkout`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },
  restoreCommit(lessonId: string, commitId: string, label = "Restore snapshot") {
    return request<CoursePackage>(`/api/lessons/${lessonId}/restore`, {
      method: "POST",
      body: JSON.stringify({ commit_id: commitId, label }),
    });
  },
  createMergeSession(
    lessonId: string,
    sourceBranchName: string,
    mode: "manual" | "ai",
    textModel: AIModelSelection
  ) {
    return request<LessonMergeSessionView>(`/api/lessons/${lessonId}/merge-sessions`, {
      method: "POST",
      body: JSON.stringify({
        source_branch_name: sourceBranchName,
        mode,
        text_model: textModel,
      }),
    });
  },
  getActiveMergeSession(lessonId: string) {
    return request<LessonMergeSessionView | null>(`/api/lessons/${lessonId}/merge-sessions/active`);
  },
  getMergeSession(lessonId: string, sessionId: string) {
    return request<LessonMergeSessionView>(`/api/lessons/${lessonId}/merge-sessions/${sessionId}`);
  },
  updateMergeSession(
    lessonId: string,
    sessionId: string,
    payload: {
      expected_version: number;
      draft_document?: LessonMergeSessionView["draft_document"];
      draft_runtime?: LessonMergeSessionView["draft_runtime"];
      resolutions?: Array<{
        conflict_id: string;
        resolution: LessonMergeResolution;
        custom_value?: unknown;
      }>;
    }
  ) {
    return request<LessonMergeSessionView>(`/api/lessons/${lessonId}/merge-sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  abandonMergeSession(lessonId: string, sessionId: string, expectedVersion: number) {
    return request<LessonMergeSessionView>(
      `/api/lessons/${lessonId}/merge-sessions/${sessionId}?expected_version=${expectedVersion}`,
      { method: "DELETE" }
    );
  },
  recomputeMergeSession(
    lessonId: string,
    sessionId: string,
    expectedVersion: number,
    textModel: AIModelSelection
  ) {
    return request<LessonMergeSessionView>(
      `/api/lessons/${lessonId}/merge-sessions/${sessionId}/recompute`,
      {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion, text_model: textModel }),
      }
    );
  },
  submitMergeSession(lessonId: string, sessionId: string, expectedVersion: number) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/merge-sessions/${sessionId}/submit`, {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
  },
  createLessonContribution(lessonId: string, title: string, description = "") {
    return request<LessonContributionView>(`/api/lessons/${lessonId}/contributions`, {
      method: "POST",
      body: JSON.stringify({ title, description }),
    });
  },
  listLessonContributions(
    role: "received" | "submitted",
    status?: LessonContributionStatus | null
  ) {
    const params = new URLSearchParams({ role });
    if (status) {
      params.set("status", status);
    }
    return request<LessonContributionView[]>(`/api/contributions?${params.toString()}`);
  },
  getLessonContribution(contributionId: string) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}`
    );
  },
  getPublicLessonContribution(contributionId: string) {
    return request<LessonContributionView>(
      `/api/public/contributions/${encodeURIComponent(contributionId)}`
    );
  },
  updateLessonContribution(
    contributionId: string,
    payload: { expected_version: number; title?: string; description?: string }
  ) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/revisions`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  addLessonContributionComment(contributionId: string, expectedVersion: number, body: string) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/comments`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion, body }) }
    );
  },
  editLessonContributionComment(
    contributionId: string,
    commentId: string,
    expectedVersion: number,
    body: string
  ) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/comments/${encodeURIComponent(commentId)}`,
      { method: "PATCH", body: JSON.stringify({ expected_version: expectedVersion, body }) }
    );
  },
  deleteLessonContributionComment(
    contributionId: string,
    commentId: string,
    expectedVersion: number
  ) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/comments/${encodeURIComponent(commentId)}`,
      { method: "DELETE", body: JSON.stringify({ expected_version: expectedVersion }) }
    );
  },
  closeLessonContribution(contributionId: string, expectedVersion: number) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/close`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) }
    );
  },
  reopenLessonContribution(contributionId: string, expectedVersion: number) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/reopen`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) }
    );
  },
  startLessonContributionMerge(contributionId: string, expectedVersion: number) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/merge/start`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) }
    );
  },
  returnLessonContributionForChanges(contributionId: string, expectedVersion: number) {
    return request<LessonContributionView>(
      `/api/contributions/${encodeURIComponent(contributionId)}/merge/return`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) }
    );
  },
  async streamMergeProposal(
    lessonId: string,
    sessionId: string,
    expectedVersion: number,
    handlers: {
      onAgentActivity?: (event: LessonMergeSessionView["agent_activity"][number]) => void;
      onFinal?: (session: LessonMergeSessionView) => void;
    },
    options?: { signal?: AbortSignal }
  ) {
    const response = await fetch(
      `${getApiBase()}/api/lessons/${lessonId}/merge-sessions/${sessionId}/ai-proposal`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ expected_version: expectedVersion }),
        cache: "no-store",
        credentials: "include",
        signal: options?.signal,
      }
    );
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, `AI merge failed with ${response.status}`));
    }
    if (!response.body) {
      throw new Error("AI merge stream returned no response body");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalSession: LessonMergeSessionView | null = null;
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const parsed = parseSseBlock(block);
        if (!parsed) {
          continue;
        }
        const data = JSON.parse(parsed.data) as unknown;
        if (parsed.event === "agent_activity") {
          handlers.onAgentActivity?.(data as LessonMergeSessionView["agent_activity"][number]);
        } else if (parsed.event === "final") {
          finalSession = data as LessonMergeSessionView;
          handlers.onFinal?.(finalSession);
        } else if (parsed.event === "error") {
          const message = data && typeof data === "object" && "message" in data
            ? String((data as { message: unknown }).message)
            : "AI merge failed";
          throw new Error(message);
        }
      }
      if (done) {
        break;
      }
    }
    if (!finalSession) {
      throw new Error("AI merge stream ended before a final proposal was saved");
    }
    return finalSession;
  },
  reorderWorkspace(orderedLessonIds: string[], activeLessonId?: string | null) {
    return request<CoursePackage>("/api/workspace/reorder", {
      method: "POST",
      body: JSON.stringify({
        ordered_lesson_ids: orderedLessonIds,
        active_lesson_id: activeLessonId ?? null,
      }),
    });
  },
  openLesson(lessonId: string) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/open`, {
      method: "POST",
    });
  },
  closeLesson(lessonId: string) {
    return request<LessonWorkspaceDelta>(`/api/lessons/${lessonId}/close?response_mode=delta`, {
      method: "POST",
    });
  },
  chatOnLesson(lessonId: string, payload: ChatRequestPayload) {
    return request<ChatResponse>(`/api/lessons/${lessonId}/chat`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  streamChatOnLesson(
    lessonId: string,
    payload: ChatRequestPayload,
    handlers: ChatStreamHandlers,
    options?: { signal?: AbortSignal }
  ) {
    return streamRequest(`/api/lessons/${lessonId}/chat/stream`, payload, handlers, options);
  },
  cancelChatOnLesson(
    lessonId: string,
    payload: { session_id: string; input_event_id: string }
  ) {
    return request<{ status: "cancel_requested"; active: boolean }>(
      `/api/lessons/${encodeURIComponent(lessonId)}/chat/cancel`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
  },
  getChatTurnStatus(
    lessonId: string,
    payload: { session_id: string; input_event_id: string }
  ) {
    const query = new URLSearchParams(payload);
    return request<{ status: "running" | "finished" }>(
      `/api/lessons/${encodeURIComponent(lessonId)}/chat/status?${query.toString()}`
    );
  },
  connectRealtime(lessonId: string, payload: RealtimeConnectPayload) {
    return request<RealtimeConnectResponse>(`/api/lessons/${lessonId}/realtime/connect`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  callRealtimeTool(lessonId: string, payload: RealtimeToolCallPayload) {
    return request<RealtimeToolCallResponse>(`/api/lessons/${lessonId}/realtime/tools`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  createGoogleRealtimeSession(lessonId: string, payload: GoogleRealtimeSessionPayload) {
    return request<GoogleRealtimeSessionResponse>(`/api/lessons/${lessonId}/realtime/google/session`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  logRealtimeEvent(lessonId: string, payload: RealtimeEventLogPayload) {
    return request<{ status: string }>(`/api/lessons/${lessonId}/realtime/events`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  logRealtimeEventBeacon(lessonId: string, payload: RealtimeEventLogPayload) {
    if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
      return false;
    }
    const blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
    return navigator.sendBeacon(
      `${getApiBase()}/api/lessons/${lessonId}/realtime/events`,
      blob
    );
  },
};
