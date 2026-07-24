import clsx from "clsx";
import { Check, Cpu, KeyRound, Landmark, UserRound } from "lucide-react";

import {
  MODEL_ACCESS_METHODS,
  PROVIDER_LABELS,
  modelAccessMethod,
  modelAccessMethodLabel,
  modelButtonLabel,
  modelOptionKey,
  modelSelectionKey,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import type { AIModelAccessMethod, AIModelOption, AIModelSelection } from "@/types";

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
  const selectedAccessMethod = modelAccessMethod(selectedOption ?? selectedModel);
  const visibleOptions = options.filter(
    (option) => modelAccessMethod(option) === selectedAccessMethod,
  );

  function selectAccessMethod(accessMethod: AIModelAccessMethod) {
    const routeOptions = options.filter(
      (option) => modelAccessMethod(option) === accessMethod && option.enabled,
    );
    const nextOption =
      routeOptions.find((option) => option.model === selectedModel.model) ??
      routeOptions.find((option) => option.default) ??
      routeOptions[0];
    if (nextOption) {
      onSelect(selectionForModelOption(nextOption, selectedModel));
    }
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400">
              当前模型
            </p>
            <p className="mt-1 text-sm font-semibold text-gray-950">
              {modelButtonLabel(selectedOption, selectedModel)}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              {modelAccessMethodLabel(selectedOption ?? selectedModel)} · 与聊天输入框共用选择状态
            </p>
          </div>
          <Cpu className="h-5 w-5 shrink-0 text-gray-400" />
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-950">调用途径</h3>
        <div className="mt-3 space-y-2">
          {MODEL_ACCESS_METHODS.map((method) => {
            const Icon = ACCESS_METHOD_ICONS[method.id];
            const enabled = options.some(
              (option) => modelAccessMethod(option) === method.id && option.enabled,
            );
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
                  !enabled && "cursor-not-allowed opacity-50",
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
                    active ? "text-gray-300" : "text-gray-500",
                  )}
                >
                  {method.description} {!enabled ? "尚未连接。" : ""}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-950">可用模型</h3>
        <div className="mt-3 space-y-2">
          {visibleOptions.map((option) => {
            const active = modelOptionKey(option) === modelSelectionKey(selectedModel);
            return (
              <button
                key={modelOptionKey(option)}
                type="button"
                disabled={!option.enabled}
                onClick={() => onSelect(selectionForModelOption(option, selectedModel))}
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
                  {PROVIDER_LABELS[option.provider]} · {option.enabled ? "可用" : "尚未配置"}
                </span>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
