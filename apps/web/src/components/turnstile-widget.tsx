"use client";

import Script from "next/script";
import { useEffect, useId, useRef, useState } from "react";

type TurnstileWidgetProps = {
  action: string;
  onTokenChange: (token: string | null) => void;
  resetKey?: number;
};

type TurnstileApi = {
  render: (container: HTMLElement, options: Record<string, unknown>) => string;
  remove: (widgetId: string) => void;
  reset: (widgetId: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const siteKey = process.env.NEXT_PUBLIC_CLOUDFLARE_TURNSTILE_SITE_KEY?.trim() || "";
const localBypassAllowed =
  process.env.NODE_ENV !== "production" || process.env.NEXT_PUBLIC_OPENCLASS_E2E_MODE === "true";

export function turnstileSubmissionReady(token: string | null) {
  return Boolean(token) || (!siteKey && localBypassAllowed);
}

export function TurnstileWidget({ action, onTokenChange, resetKey = 0 }: TurnstileWidgetProps) {
  const reactId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const onTokenChangeRef = useRef(onTokenChange);
  const [scriptReady, setScriptReady] = useState(false);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    onTokenChangeRef.current = onTokenChange;
  }, [onTokenChange]);

  useEffect(() => {
    if (!siteKey || !scriptReady || !containerRef.current || !window.turnstile) {
      return;
    }
    const turnstile = window.turnstile;
    widgetIdRef.current = turnstile.render(containerRef.current, {
      sitekey: siteKey,
      action,
      theme: "light",
      size: "flexible",
      callback: (token: string) => onTokenChangeRef.current(token),
      "expired-callback": () => {
        onTokenChangeRef.current(null);
        if (widgetIdRef.current) {
          turnstile.reset(widgetIdRef.current);
        }
      },
      "error-callback": () => {
        onTokenChangeRef.current(null);
        return true;
      },
    });

    return () => {
      if (widgetIdRef.current) {
        turnstile.remove(widgetIdRef.current);
        widgetIdRef.current = null;
      }
    };
  }, [action, scriptReady]);

  useEffect(() => {
    if (resetKey > 0 && widgetIdRef.current && window.turnstile) {
      window.turnstile.reset(widgetIdRef.current);
      onTokenChangeRef.current(null);
    }
  }, [resetKey]);

  if (!siteKey) {
    return (
      <p
        className={`rounded-lg border px-3 py-2 text-xs leading-5 ${
          localBypassAllowed
            ? "border-amber-200 bg-amber-50 text-amber-800"
            : "border-rose-200 bg-rose-50 text-rose-700"
        }`}
        role={localBypassAllowed ? "status" : "alert"}
      >
        {localBypassAllowed
          ? "本地开发未配置 Cloudflare Turnstile，已跳过人机验证。"
          : "人机验证尚未配置，当前无法提交。请联系管理员。"}
      </p>
    );
  }

  return (
    <div className="min-h-[65px]" aria-label="Cloudflare Turnstile 人机验证">
      <Script
        id={`cloudflare-turnstile-${reactId.replaceAll(":", "")}`}
        src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
        strategy="afterInteractive"
        onReady={() => {
          setLoadError(false);
          setScriptReady(true);
        }}
        onError={() => {
          setLoadError(true);
          onTokenChangeRef.current(null);
        }}
      />
      <div ref={containerRef} />
      {loadError ? <p className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" role="alert">人机验证加载失败，请检查网络后刷新页面。</p> : null}
    </div>
  );
}
