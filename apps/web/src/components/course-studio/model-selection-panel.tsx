import clsx from "clsx";
import { Check, Cpu, KeyRound, Landmark, Trash2, UserRound } from "lucide-react";
import { useEffect, useState } from "react";

import {
  MODEL_ACCESS_METHODS,
  MODEL_CREDENTIALS_CHANGED_EVENT,
  PROVIDER_LABELS,
  modelAccessMethod,
  modelAccessMethodLabel,
  modelButtonLabel,
  modelOptionKey,
  modelSelectionKey,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import { api } from "@/lib/api";
import type {
  AIModelAccessMethod,
  AIModelOption,
  AIModelSelection,
  AIProvider,
  AIProviderCredentialStatus,
} from "@/types";

const ACCESS_METHOD_ICONS = {
  chatgpt_subscription: UserRound,
  personal_api: KeyRound,
  platform_credits: Landmark,
} satisfies Record<AIModelAccessMethod, typeof UserRound>;

type ModelSelectionPanelProps = {
  options: AIModelOption[];
  selectedModel: AIModelSelection;
  selectedOption: AIModelOption | null;
  onSelect: (selection: AIModelSelection) => void;
};

export function ModelSelectionPanel({
  options,
  selectedModel,
  selectedOption,
  onSelect,
}: ModelSelectionPanelProps) {
  const [credentials, setCredentials] = useState<AIProviderCredentialStatus[]>([]);
  const [credentialDrafts, setCredentialDrafts] = useState<
    Partial<Record<AIProvider, string>>
  >({});
  const [credentialBusy, setCredentialBusy] = useState<AIProvider | null>(null);
  const [credentialMessage, setCredentialMessage] = useState("");
  const selectedAccessMethod = modelAccessMethod(selectedOption ?? selectedModel);
  const visibleOptions = options.filter(
    (option) => modelAccessMethod(option) === selectedAccessMethod
  );

  useEffect(() => {
    let cancelled = false;
    api
      .getModelCredentials()
      .then((statuses) => {
        if (!cancelled) {
          setCredentials(statuses);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setCredentialMessage(
            error instanceof Error ? error.message : "Unable to read personal API status"
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function selectAccessMethod(accessMethod: AIModelAccessMethod) {
    const routeOptions = options.filter(
      (option) => modelAccessMethod(option) === accessMethod && option.enabled
    );
    const nextOption =
      routeOptions.find((option) => option.model === selectedModel.model) ??
      routeOptions.find((option) => option.default) ??
      routeOptions[0];
    if (nextOption) {
      onSelect(selectionForModelOption(nextOption, selectedModel));
    }
  }

  function updateCredentialStatus(status: AIProviderCredentialStatus) {
    setCredentials((current) =>
      current.map((item) => (item.provider === status.provider ? status : item))
    );
    window.dispatchEvent(new Event(MODEL_CREDENTIALS_CHANGED_EVENT));
  }

  async function saveCredential(provider: AIProvider) {
    const apiKey = credentialDrafts[provider]?.trim() ?? "";
    if (!apiKey) {
      setCredentialMessage("Please enter API Key");
      return;
    }
    setCredentialBusy(provider);
    setCredentialMessage("");
    try {
      const status = await api.saveModelCredential(provider, apiKey);
      updateCredentialStatus(status);
      setCredentialDrafts((current) => ({ ...current, [provider]: "" }));
      setCredentialMessage(`${status.label} API key connected`);
    } catch (error) {
      setCredentialMessage(
        error instanceof Error ? error.message : "API Key failed to save"
      );
    } finally {
      setCredentialBusy(null);
    }
  }

  async function deleteCredential(provider: AIProvider) {
    setCredentialBusy(provider);
    setCredentialMessage("");
    try {
      const status = await api.deleteModelCredential(provider);
      updateCredentialStatus(status);
      setCredentialMessage(`${status.label} API key removed`);
    } catch (error) {
      setCredentialMessage(
        error instanceof Error ? error.message : "API Key deletion failed"
      );
    } finally {
      setCredentialBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400">

              current model
            </p>
            <p className="mt-1 text-sm font-semibold text-gray-950">
              {modelButtonLabel(selectedOption, selectedModel)}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              {modelAccessMethodLabel(selectedOption ?? selectedModel)}  · Share selection state with chat input box
            </p>
          </div>
          <Cpu className="h-5 w-5 shrink-0 text-gray-400" />
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-950">Calling route</h3>
        <div className="mt-3 space-y-2">
          {MODEL_ACCESS_METHODS.map((method) => {
            const Icon = ACCESS_METHOD_ICONS[method.id];
            const routeEnabled = options.some(
              (option) =>
                modelAccessMethod(option) === method.id && option.enabled
            );
            const canConfigurePersonal =
              method.id === "personal_api" &&
              credentials.some((credential) => credential.manageable);
            const enabled = routeEnabled || canConfigurePersonal;
            const active = selectedAccessMethod === method.id;
            return (
              <button
                key={method.id}
                type="button"
                disabled={!enabled}
                aria-pressed={active}
                onClick={() => selectAccessMethod(method.id)}
                className={clsx(
                  "w-full rounded-lg border p-3 text-left transition",
                  active
                    ? "border-gray-950 bg-gray-950 text-white"
                    : "border-gray-200 bg-white hover:border-gray-400",
                  !enabled && "cursor-not-allowed opacity-50"
                )}
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className="font-semibold">{method.label}</span>
                  {active ? <Check className="ml-auto h-4 w-4" /> : null}
                </span>
                <span
                  className={clsx(
                    "mt-1 block text-xs leading-5",
                    active ? "text-gray-300" : "text-gray-500"
                  )}
                >
                  {method.description}
                  {!routeEnabled && !canConfigurePersonal ? "Not connected yet." : ""}
                </span>
              </button>
            );
          })}
        </div>

        <div className="mt-4 border-t border-gray-100 pt-4">
          <div className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-gray-500" />
            <h4 className="text-sm font-semibold text-gray-950">Personal API Key</h4>
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">

            Key is only saved to the private model directory of the current account and will not be echoed on the page.
          </p>
          <div className="mt-3 space-y-3">
            {credentials.map((credential) => (
              <div
                key={credential.provider}
                className="rounded-lg border border-gray-200 bg-gray-50 p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <label
                    htmlFor={`model-api-key-${credential.provider}`}
                    className="text-sm font-semibold text-gray-900"
                  >
                    {credential.label} API Key
                  </label>
                  <span
                    className={clsx(
                      "text-xs font-medium",
                      credential.configured ? "text-emerald-700" : "text-gray-400"
                    )}
                  >
                    {credential.configured ? "Connected" : "Not connected"}
                  </span>
                </div>
                <input
                  id={`model-api-key-${credential.provider}`}
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  disabled={!credential.manageable || credentialBusy === credential.provider}
                  value={credentialDrafts[credential.provider] ?? ""}
                  onChange={(event) =>
                    setCredentialDrafts((current) => ({
                      ...current,
                      [credential.provider]: event.target.value,
                    }))
                  }
                  placeholder={credential.configured ? "Enter a new Key to replace" : "Enter API Key"}
                  className="mt-2 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none transition focus:border-gray-500 disabled:cursor-not-allowed disabled:bg-gray-100"
                />
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    disabled={
                      !credential.manageable ||
                      credentialBusy === credential.provider ||
                      !(credentialDrafts[credential.provider] ?? "").trim()
                    }
                    onClick={() => void saveCredential(credential.provider)}
                    className="rounded-lg bg-gray-950 px-3 py-2 text-xs font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {credentialBusy === credential.provider
                      ? "Saving"
                      : credential.configured
                        ? "Replace Key"
                        : "SaveKey"}
                  </button>
                  {credential.configured ? (
                    <button
                      type="button"
                      disabled={credentialBusy === credential.provider}
                      onClick={() => void deleteCredential(credential.provider)}
                      className="inline-flex items-center gap-1 rounded-lg px-3 py-2 text-xs font-semibold text-red-600 transition hover:bg-red-50 disabled:opacity-40"
                    >
                      <Trash2 className="h-3.5 w-3.5" />

                      delete
                    </button>
                  ) : null}
                </div>
                {!credential.manageable ? (
                  <p className="mt-2 text-xs text-amber-700">

                    You can save your personal API Key after logging in to your account.
                  </p>
                ) : null}
              </div>
            ))}
          </div>
          {credentialMessage ? (
            <p role="status" className="mt-3 text-xs leading-5 text-gray-600">
              {credentialMessage}
            </p>
          ) : null}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-950">Available models</h3>
        <div className="mt-3 space-y-2">
          {visibleOptions.map((option) => {
            const active =
              modelOptionKey(option) === modelSelectionKey(selectedModel);
            return (
              <button
                key={modelOptionKey(option)}
                type="button"
                disabled={!option.enabled}
                onClick={() =>
                  onSelect(selectionForModelOption(option, selectedModel))
                }
                className={clsx(
                  "w-full rounded-lg border p-3 text-left transition",
                  active
                    ? "border-gray-950 bg-gray-950 text-white"
                    : "border-gray-200 bg-white hover:border-gray-400",
                  !option.enabled && "cursor-not-allowed opacity-50",
                )}
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="font-semibold">{option.label}</span>
                  {active ? <Check className="h-4 w-4" /> : null}
                </span>
                <span
                  className={clsx(
                    "mt-1 block text-xs",
                    active ? "text-gray-300" : "text-gray-500",
                  )}
                >
                  {PROVIDER_LABELS[option.provider]} · {option.enabled ? "Available" : "Not configured yet"}
                </span>
                {selectedAccessMethod === "platform_credits" ? (
                  <span
                    className={clsx(
                      "mt-1.5 block text-xs font-medium",
                      active ? "text-emerald-300" : "text-emerald-700",
                    )}
                  >

                    Every 1 million input tokens ·{" "}
                    {option.input_price_credits_per_million == null
                      ? "Price is not available yet"
                      : `${option.input_price_credits_per_million.toLocaleString("en-US")} platform credits`}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
