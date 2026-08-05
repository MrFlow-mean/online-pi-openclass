"use client";

import clsx from "clsx";
import { Check, LoaderCircle, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

type InlineNameFormVariant = "sidebar" | "tab";

type InlineNameFormProps = {
  label: string;
  placeholder: string;
  confirmLabel?: string;
  cancelLabel?: string;
  isBusy?: boolean;
  variant?: InlineNameFormVariant;
  className?: string;
  onCancel: () => void;
  onSubmit: (value: string) => void | boolean | Promise<void | boolean>;
};

export function InlineNameForm({
  label,
  placeholder,
  confirmLabel = "confirm",
  cancelLabel = "Cancel",
  isBusy = false,
  variant = "sidebar",
  className,
  onCancel,
  onSubmit,
}: InlineNameFormProps) {
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const trimmedDraft = draft.trim();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trimmedDraft || isBusy) {
      return;
    }
    await onSubmit(trimmedDraft);
  }

  return (
    <form
      onSubmit={(event) => void handleSubmit(event)}
      className={clsx(
        "flex items-center gap-2 border bg-white transition-colors",
        variant === "tab"
          ? "mx-1.5 h-[30px] w-[260px] max-w-[36vw] rounded-md border-gray-200 bg-gray-50/80 px-2 shadow-sm focus-within:border-stone-300 focus-within:bg-white"
          : "rounded-2xl border-stone-200 px-3 py-2 shadow-sm",
        className
      )}
    >
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !isBusy) {
            event.preventDefault();
            onCancel();
          }
        }}
        disabled={isBusy}
        aria-label={label}
        placeholder={placeholder}
        className={clsx(
          "min-w-0 flex-1 bg-transparent text-stone-950 outline-none placeholder:text-stone-400 disabled:text-stone-400",
          variant === "tab" ? "text-xs font-semibold" : "text-sm font-semibold"
        )}
      />
      <button
        type="submit"
        disabled={!trimmedDraft || isBusy}
        title={confirmLabel}
        aria-label={confirmLabel}
        className={clsx(
          "flex shrink-0 items-center justify-center transition disabled:cursor-not-allowed",
          variant === "tab"
            ? "h-6 w-6 rounded-md bg-stone-900 text-white hover:bg-stone-700 disabled:bg-stone-200 disabled:text-stone-400"
            : "h-8 w-8 rounded-xl bg-stone-950 text-white hover:bg-stone-800 disabled:bg-stone-200 disabled:text-stone-400"
        )}
      >
        {isBusy ? (
          <LoaderCircle className={clsx("animate-spin", variant === "tab" ? "h-3.5 w-3.5" : "h-4 w-4")} />
        ) : (
          <Check className={clsx(variant === "tab" ? "h-3.5 w-3.5" : "h-4 w-4")} />
        )}
      </button>
      <button
        type="button"
        onClick={onCancel}
        disabled={isBusy}
        title={cancelLabel}
        aria-label={cancelLabel}
        className={clsx(
          "flex shrink-0 items-center justify-center text-stone-400 transition hover:bg-stone-100 hover:text-stone-950 disabled:cursor-not-allowed disabled:text-stone-300",
          variant === "tab" ? "h-6 w-6 rounded-md" : "h-8 w-8 rounded-xl"
        )}
      >
        <X className={clsx(variant === "tab" ? "h-3.5 w-3.5" : "h-4 w-4")} />
      </button>
    </form>
  );
}
