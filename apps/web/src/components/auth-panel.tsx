"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import type { CSSProperties, FormEvent } from "react";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  ArrowLeft,
  ArrowUp,
  Atom,
  BarChart3,
  Dna,
  FlaskConical,
  GraduationCap,
  Hexagon,
  LoaderCircle,
  LockKeyhole,
  Mail,
  Magnet,
  Microscope,
  Network,
  PenTool,
  PieChart,
  Pill,
  ShieldCheck,
  Sparkles,
  Telescope,
  TestTubes,
  User,
  WandSparkles,
  type LucideIcon,
} from "lucide-react";

import {
  api,
  clearAuthToken,
  getApiBase,
  readAuthToken,
  readGuestAuthToken,
  storeAuthToken,
  storeGuestAuthToken,
} from "@/lib/api";
import { BrandMark } from "@/components/brand-mark";
import { TurnstileWidget, turnstileSubmissionReady } from "@/components/turnstile-widget";
import { userAccountLabel } from "@/lib/account";
import { loginRedirectPath } from "@/lib/auth-redirect";
import {
  CODEX_LOGIN_POLL_INTERVAL_MS,
  clearPendingCodexLogin,
  pendingCodexLoginExpired,
  readPendingCodexLogin,
  storePendingCodexLogin,
} from "@/lib/codex-login-persistence";
import type { AuthProviderView, CodexLoginStartResponse, UserView } from "@/types";

type AuthPanelProps = {
  initialMode: "register" | "login";
};

type AuthCssVars = CSSProperties & {
  "--base-op"?: string;
  "--rot"?: string;
};

type SocialSignInOption = {
  id: string;
  label: string;
  providerLabel: string;
  className: string;
  brand: "apple" | "github" | "google" | "microsoft" | "x";
};

type KnowledgeTextItem = {
  className: string;
  content: string;
  style?: AuthCssVars;
};

type KnowledgeIconItem = {
  className: string;
  Icon: LucideIcon;
  style?: AuthCssVars;
};

const authProviderRetryDelaysMs = [0, 250, 750] as const;

async function loadAuthProvidersWithRetry() {
  let lastError: unknown = new Error("Could not load sign-in methods");
  for (const delayMs of authProviderRetryDelaysMs) {
    if (delayMs > 0) {
      await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    }
    try {
      return await api.getAuthProviders();
    } catch (providerError) {
      lastError = providerError;
    }
  }
  throw lastError;
}

const socialSignInOptions: SocialSignInOption[] = [
  {
    id: "google",
    label: "Sign in with Google",
    providerLabel: "Google account",
    className: "border-[#e8dfd2] bg-white text-[#5c4c3c] hover:border-[#d2a878] hover:bg-[#fcfbf9]",
    brand: "google",
  },
  {
    id: "github",
    label: "Sign in with GitHub",
    providerLabel: "GitHub account",
    className: "border-[#24292f] bg-[#24292f] text-white hover:bg-black",
    brand: "github",
  },
];

const knowledgeTextItems: KnowledgeTextItem[] = [
  {
    content: "∮ E · dA = Q/ε₀",
    className: "auth-float-orbit auth-fade-breathe absolute left-[10%] top-[5%] font-serif text-3xl text-[#5c4c3c] md:text-4xl",
    style: { "--base-op": "0.08", "--rot": "-10deg" },
  },
  {
    content: "∑(x_i - μ)²",
    className: "auth-float-diag absolute right-[5%] top-[35%] font-serif text-4xl text-[#5c4c3c] opacity-[0.08] md:text-5xl",
    style: { "--rot": "5deg" },
  },
  {
    content: "iℏ(∂Ψ/∂t) = HΨ",
    className: "auth-float-wave absolute bottom-[10%] left-[15%] font-serif text-3xl text-[#cbaa77] opacity-[0.12] md:text-4xl",
    style: { "--rot": "-5deg" },
  },
  {
    content: "π",
    className: "auth-rotate-spin auth-fade-breathe absolute left-[45%] top-[15%] font-serif text-8xl text-[#6d93a7] md:text-9xl",
    style: { "--base-op": "0.05", "--rot": "15deg" },
  },
  {
    content: "e^{iπ} + 1 = 0",
    className: "auth-float-up absolute bottom-[25%] right-[25%] font-serif text-5xl text-[#5c4c3c] opacity-[0.08] md:text-6xl",
    style: { "--rot": "10deg" },
  },
  {
    content: "∇ × B = μ₀J",
    className: "auth-float-fast-drift absolute left-[5%] top-[50%] font-serif text-4xl text-[#708f73] opacity-10 md:text-5xl",
    style: { "--rot": "-15deg" },
  },
  {
    content: "Investigate things to gain knowledge",
    className: "auth-scale-grow absolute left-[15%] top-[20%] text-4xl font-bold text-[#5c4c3c] opacity-10 md:text-5xl",
    style: { "--rot": "12deg" },
  },
  {
    content: "العربية",
    className: "auth-float-orbit auth-fade-breathe absolute bottom-[35%] left-[8%] font-serif text-3xl text-[#8d8377] md:text-4xl",
    style: { "--base-op": "0.12", "--rot": "-8deg" },
  },
  {
    content: "Наука",
    className: "auth-float-diag absolute right-[8%] top-[65%] font-serif text-2xl text-[#cbaa77] opacity-[0.15] md:text-3xl",
    style: { "--rot": "-20deg" },
  },
  {
    content: "Japanese",
    className: "auth-float-wave absolute right-[40%] top-[8%] text-3xl text-[#6d93a7] opacity-[0.12] md:text-4xl",
    style: { "--rot": "5deg" },
  },
  {
    content: "Ελληνικά",
    className: "auth-float-up absolute bottom-[15%] right-[45%] font-serif text-2xl text-[#708f73] opacity-[0.15] md:text-3xl",
    style: { "--rot": "-12deg" },
  },
  {
    content: "λ",
    className: "auth-float-diag absolute bottom-[30%] right-[35%] font-serif text-4xl text-[#708f73] opacity-[0.15] md:text-5xl",
    style: { "--rot": "20deg" },
  },
  {
    content: "Au",
    className:
      "auth-rotate-spin absolute right-[18%] top-[18%] flex h-12 w-12 items-center justify-center border-2 border-[#d2a878] font-serif text-xl text-[#d2a878] opacity-[0.15]",
    style: { "--rot": "5deg" },
  },
];

const knowledgeIconItems: KnowledgeIconItem[] = [
  {
    Icon: Dna,
    className: "auth-float-up auth-fade-breathe absolute left-[25%] top-[12%] text-4xl text-[#6d93a7] md:text-5xl",
    style: { "--base-op": "0.12", "--rot": "25deg" },
  },
  {
    Icon: FlaskConical,
    className: "auth-float-diag auth-fade-breathe absolute right-[25%] top-[28%] text-5xl text-[#708f73] md:text-6xl",
    style: { "--base-op": "0.15", "--rot": "15deg" },
  },
  {
    Icon: TestTubes,
    className: "auth-float-fast-drift absolute bottom-[40%] left-[12%] text-4xl text-[#708f73] opacity-[0.15] md:text-5xl",
    style: { "--rot": "-15deg" },
  },
  {
    Icon: Atom,
    className: "auth-rotate-spin absolute right-[45%] top-[42%] text-5xl text-[#cbaa77] opacity-[0.15] md:text-7xl",
    style: { "--rot": "-10deg" },
  },
  {
    Icon: Telescope,
    className: "auth-float-wave auth-fade-breathe absolute bottom-[50%] right-[8%] text-4xl text-[#5c4c3c] md:text-5xl",
    style: { "--base-op": "0.08", "--rot": "10deg" },
  },
  {
    Icon: Microscope,
    className: "auth-scale-grow absolute bottom-[5%] right-[15%] text-5xl text-[#6d93a7] opacity-[0.12] md:text-6xl",
  },
  {
    Icon: Magnet,
    className: "auth-float-fast-drift absolute right-[12%] top-[60%] text-4xl text-[#8d8377] opacity-[0.12]",
    style: { "--rot": "45deg" },
  },
  {
    Icon: BarChart3,
    className: "auth-scale-grow auth-fade-breathe absolute left-[30%] top-[75%] text-4xl text-[#5c4c3c] md:text-5xl",
    style: { "--base-op": "0.08" },
  },
  {
    Icon: Hexagon,
    className: "auth-float-orbit absolute right-[10%] top-[15%] text-4xl text-[#8d8377] opacity-[0.12] md:text-5xl",
    style: { "--rot": "45deg" },
  },
  {
    Icon: Network,
    className: "auth-float-fast-drift absolute bottom-[20%] left-[35%] text-5xl text-[#cbaa77] opacity-[0.12] md:text-6xl",
    style: { "--rot": "15deg" },
  },
  {
    Icon: PieChart,
    className: "auth-float-wave absolute right-[25%] top-[85%] text-4xl text-[#6d93a7] opacity-[0.15]",
    style: { "--rot": "-15deg" },
  },
  {
    Icon: Pill,
    className: "auth-float-wave absolute right-[30%] top-[80%] text-4xl text-[#cbaa77] opacity-[0.15]",
    style: { "--rot": "-10deg" },
  },
];

function GoogleBrandIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 18 18" className="h-[18px] w-[18px]">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.56 2.69-3.86 2.69-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.81.54-1.84.86-3.05.86-2.35 0-4.33-1.58-5.04-3.71H.96v2.33A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.96 10.71A5.41 5.41 0 0 1 3.68 9c0-.59.1-1.16.28-1.71V4.96H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.04l3-2.33Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.43 1.35l2.58-2.58A8.64 8.64 0 0 0 9 0 9 9 0 0 0 .96 4.96l3 2.33C4.67 5.16 6.65 3.58 9 3.58Z"
      />
    </svg>
  );
}

function AppleBrandIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-[18px] w-[18px]" fill="currentColor">
      <path d="M11.18.01c-.03-.04-1.26.02-2.32 1.17-1.07 1.16-.91 2.49-.88 2.52.02.03 1.52.09 2.47-1.26.96-1.34.76-2.39.73-2.43ZM14.5 11.74c-.05-.1-2.33-1.24-2.12-3.42.22-2.19 1.68-2.79 1.7-2.86.02-.06-.6-.79-1.25-1.15a4.48 4.48 0 0 0-2.21-.56c-.25 0-1.13.14-1.72.14s-1.43-.14-2.17-.1c-.74.03-1.9.44-2.71 1.29-.8.86-1.89 2.5-1.61 5.16.28 2.65 2.21 5.56 3.18 5.59.97.03 1.32-.62 2.77-.62 1.45 0 1.74.62 2.83.6 1.1-.01 1.84-1.09 2.54-2.17.69-1.08.82-1.84.77-1.9Z" />
    </svg>
  );
}

function GitHubBrandIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-[18px] w-[18px]" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.67 0 8.2c0 3.62 2.29 6.69 5.47 7.78.4.08.55-.18.55-.4 0-.2-.01-.86-.01-1.56-2.01.38-2.53-.5-2.69-.95-.09-.23-.48-.95-.82-1.14-.28-.16-.68-.55-.01-.56.63-.01 1.08.59 1.23.83.72 1.24 1.87.89 2.33.68.07-.53.28-.89.51-1.09-1.78-.21-3.64-.91-3.64-4.03 0-.89.31-1.62.82-2.19-.08-.21-.36-1.04.08-2.16 0 0 .67-.22 2.2.84A7.4 7.4 0 0 1 8 3.98c.68 0 1.36.09 2 .27 1.53-1.06 2.2-.84 2.2-.84.44 1.12.16 1.95.08 2.16.51.57.82 1.3.82 2.19 0 3.13-1.87 3.82-3.65 4.03.29.26.54.76.54 1.54 0 1.11-.01 2.01-.01 2.28 0 .22.15.48.55.4A8.15 8.15 0 0 0 16 8.2C16 3.67 12.42 0 8 0Z" />
    </svg>
  );
}

function MicrosoftBrandIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 18 18" className="h-[18px] w-[18px]">
      <path fill="#f35325" d="M1 1h7.5v7.5H1z" />
      <path fill="#81bc06" d="M9.5 1H17v7.5H9.5z" />
      <path fill="#05a6f0" d="M1 9.5h7.5V17H1z" />
      <path fill="#ffba08" d="M9.5 9.5H17V17H9.5z" />
    </svg>
  );
}

function XBrandIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 18 18" className="h-[18px] w-[18px]" fill="currentColor">
      <path d="M10.7 7.63 17.37 0h-1.58L10 6.62 5.37 0H0l7 10.01L0 18h1.58l6.12-6.99L12.6 18H18L10.7 7.63Zm-2.17 2.48-.71-.99L2.18 1.18h2.43l4.55 6.4.71.99 5.92 8.34h-2.43l-4.83-6.8Z" />
    </svg>
  );
}

function SocialBrandIcon({ brand }: { brand: SocialSignInOption["brand"] }) {
  if (brand === "google") {
    return <GoogleBrandIcon />;
  }
  if (brand === "apple") {
    return <AppleBrandIcon />;
  }
  if (brand === "github") {
    return <GitHubBrandIcon />;
  }
  if (brand === "microsoft") {
    return <MicrosoftBrandIcon />;
  }
  if (brand === "x") {
    return <XBrandIcon />;
  }
  return null;
}

function loginDestination(nextPath: string | null) {
  return loginRedirectPath(nextPath);
}

function navigateAfterAuth(path: string, mode: "assign" | "replace" = "assign") {
  if (typeof window === "undefined") {
    return;
  }
  if (mode === "replace") {
    window.location.replace(path);
    return;
  }
  window.location.assign(path);
}

function subscribeToLocationSearch(onStoreChange: () => void) {
  window.addEventListener("popstate", onStoreChange);
  return () => window.removeEventListener("popstate", onStoreChange);
}

function getLocationSearch() {
  return typeof window === "undefined" ? null : window.location.search;
}

function getServerLocationSearch() {
  return null;
}

function AuthInput({
  autoComplete,
  Icon,
  id,
  inputMode,
  label,
  maxLength,
  minLength,
  onChange,
  placeholder,
  pattern,
  type,
  value,
}: {
  autoComplete: string;
  Icon: LucideIcon;
  id: string;
  inputMode?: "email" | "numeric" | "text";
  label: string;
  maxLength?: number;
  minLength?: number;
  onChange: (value: string) => void;
  placeholder: string;
  pattern?: string;
  type: "email" | "password" | "text";
  value: string;
}) {
  return (
    <label className="block">
      {label ? <span className="text-sm font-semibold text-[#5c4c3c]">{label}</span> : null}
      <span className="mt-2 flex items-center rounded-lg border border-[#ebe2d2] bg-white shadow-sm transition focus-within:border-[#d2a878] focus-within:ring-2 focus-within:ring-[#d2a878]/25">
        <Icon className="ml-3.5 h-[18px] w-[18px] text-[#decbae]" />
        <input
          id={id}
          type={type}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          required
          inputMode={inputMode}
          maxLength={maxLength}
          minLength={minLength}
          pattern={pattern}
          autoComplete={autoComplete}
          className="min-w-0 flex-1 bg-transparent px-3 py-3 text-sm font-medium text-[#3a312b] outline-none placeholder:text-[#b8a58f] sm:py-3.5"
          placeholder={placeholder}
        />
      </span>
    </label>
  );
}

function KnowledgeBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 z-0 overflow-hidden">
      {knowledgeTextItems.map((item) => (
        <div key={item.content} className={item.className} style={item.style}>
          {item.content}
        </div>
      ))}

      {knowledgeIconItems.map(({ Icon, className, style }) => (
        <Icon key={className} className={className} style={style} strokeWidth={1.6} />
      ))}

      <div className="auth-float-orbit auth-fade-breathe absolute right-[15%] top-[55%] text-[#d2a878]" style={{ "--base-op": "0.15", "--rot": "5deg" } as AuthCssVars}>
        <svg width="120" height="120" viewBox="0 0 120 120" fill="none" stroke="currentColor" strokeWidth="0.5" className="h-28 w-28 md:h-36 md:w-36">
          <circle cx="60" cy="60" r="40" />
          <ellipse cx="60" cy="60" rx="40" ry="15" transform="rotate(45 60 60)" />
          <ellipse cx="60" cy="60" rx="40" ry="15" transform="rotate(-45 60 60)" />
          <ellipse cx="60" cy="60" rx="40" ry="15" transform="rotate(90 60 60)" />
          <polygon points="60,20 100,60 60,100 20,60" />
        </svg>
      </div>

      <div className="auth-float-up absolute bottom-[20%] right-[48%] text-[#6d93a7] opacity-[0.18]" style={{ "--rot": "-15deg" } as AuthCssVars}>
        <svg width="100" height="100" viewBox="0 0 100 100" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-20 w-20 md:h-24 md:w-24">
          <polygon points="50,20 20,80 80,80" />
          <path d="M 0 60 L 35 60" strokeDasharray="2 2" />
          <path d="M 65 60 L 100 40" stroke="#8d8377" strokeDasharray="2 2" />
          <path d="M 65 60 L 100 60" stroke="#708f73" strokeDasharray="2 2" />
          <path d="M 65 60 L 100 80" stroke="#6d93a7" strokeDasharray="2 2" />
        </svg>
      </div>

      <div className="auth-float-diag absolute left-[30%] top-[35%] text-[#5c4c3c] opacity-[0.08]" style={{ "--rot": "10deg" } as AuthCssVars}>
        <svg width="80" height="80" viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeWidth="1" className="h-16 w-16">
          {[10, 32, 54].map((x) =>
            [10, 32, 54].map((y) => <rect key={`${x}-${y}`} x={x} y={y} width="15" height="15" />)
          )}
        </svg>
      </div>
    </div>
  );
}

function ChatShowcase() {
  return (
    <div
      className="absolute left-10 top-20 z-10 flex h-[520px] w-[340px] flex-col rounded-lg border border-white/60 bg-white/40 p-6 shadow-[0_30px_60px_-15px_rgba(58,49,43,0.09)] backdrop-blur-2xl"
      style={{ transform: "translateZ(-80px)" }}
    >
      <div className="mb-8 flex items-center gap-3 border-b border-[#3a312b]/5 pb-4">
        <span className="h-2 w-2 rounded-full bg-[#d2a878]" />
        <span className="text-xs font-bold text-[#5c4c3c]/60">AI Logic Engine</span>
      </div>

      <div className="relative flex-1 overflow-hidden">
        <div className="auth-loop auth-chat-scroll absolute left-0 top-4 w-full space-y-6">
          <div className="auth-loop auth-chat-user-1 flex flex-row-reverse gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#3a312b] shadow-md">
              <User className="h-4 w-4 text-white" />
            </div>
            <div className="flex w-full flex-col items-end gap-2 pt-1.5">
              <div className="auth-skeleton-dark h-2.5 w-[65%] rounded-full" />
              <div className="auth-skeleton-dark h-2.5 w-[45%] rounded-full" />
            </div>
          </div>

          <div className="auth-loop auth-chat-ai-1 flex gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white bg-[#ebe2d2] shadow-sm">
              <Sparkles className="h-4 w-4 text-[#5c4c3c]" />
            </div>
            <div className="flex w-full flex-col gap-2 rounded-lg border border-white/60 bg-white/50 p-3 pt-3.5">
              <div className="auth-skeleton h-2 w-[85%] rounded-full" />
              <div className="auth-skeleton h-2 w-[70%] rounded-full" />
              <div className="auth-skeleton h-2 w-[90%] rounded-full" />
              <div className="mt-2 flex gap-2">
                <div className="h-8 w-10 rounded-lg border border-white bg-[#f7f3eb]/80" />
                <div className="h-8 w-10 rounded-lg border border-white bg-[#f7f3eb]/80" />
              </div>
            </div>
          </div>

          <div className="auth-loop auth-chat-user-2 mt-8 flex flex-row-reverse gap-3">
            <div className="h-8 w-8 shrink-0 rounded-full bg-[#3a312b] shadow-md" />
            <div className="flex w-full flex-col items-end gap-2 pt-1.5">
              <div className="auth-skeleton-dark h-2.5 w-[80%] rounded-full" />
            </div>
          </div>

          <div className="auth-loop auth-chat-ai-2 flex gap-3">
            <div className="h-8 w-8 shrink-0 rounded-full border border-white bg-[#ebe2d2] shadow-sm" />
            <div className="flex w-full flex-col gap-2 rounded-lg border border-white/60 bg-white/50 p-3 pt-3.5">
              <div className="auth-skeleton h-2 w-full rounded-full" />
              <div className="auth-skeleton h-2 w-[60%] rounded-full" />
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 flex h-12 items-center rounded-lg border border-white bg-white/60 px-4">
        <span className="mr-3 h-4 w-4 rounded-full border-2 border-[#decbae]" />
        <span className="h-2 w-1/3 rounded-full bg-[#ebe2d2]" />
        <span className="ml-auto flex h-6 w-6 items-center justify-center rounded-full bg-[#3a312b]">
          <ArrowUp className="h-3.5 w-3.5 text-white" />
        </span>
      </div>
    </div>
  );
}

function DocumentShowcase() {
  return (
    <div
      className="absolute right-10 top-10 z-20 h-[600px] w-[500px] overflow-hidden rounded-lg border border-[#f0e8dc] bg-[#fefdfb] p-10 shadow-[0_40px_80px_-20px_rgba(58,49,43,0.16),inset_0_0_0_1px_rgba(255,255,255,0.8)]"
      style={{ transform: "translateZ(60px)" }}
    >
      <div className="mb-10 flex items-center justify-between opacity-60">
        <div className="flex gap-2">
          <span className="h-3 w-3 rounded-full bg-[#ebe2d2]" />
          <span className="h-3 w-3 rounded-full bg-[#ebe2d2]" />
        </div>
        <div className="flex gap-4">
          <span className="h-1 w-8 rounded-full bg-[#ebe2d2]" />
          <span className="h-1 w-12 rounded-full bg-[#ebe2d2]" />
          <span className="h-1 w-6 rounded-full bg-[#ebe2d2]" />
        </div>
      </div>

      <div className="auth-loop auth-fade-end relative h-[450px] w-full">
        <svg viewBox="0 0 420 450" fill="none" className="h-full w-full overflow-visible">
          <path d="M40 0 V450 M380 0 V450" stroke="#f7f3eb" strokeWidth="1" strokeDasharray="4 6" />
          <g stroke="#d8c9b6" strokeLinecap="round" opacity="0.16">
            <line x1="40" y1="40" x2="260" y2="40" strokeWidth="14" />
            <line x1="40" y1="65" x2="140" y2="65" strokeWidth="6" />
            <line x1="40" y1="110" x2="360" y2="110" strokeWidth="6" />
            <line x1="40" y1="135" x2="380" y2="135" strokeWidth="6" />
            <line x1="40" y1="160" x2="330" y2="160" strokeWidth="6" />
            <line x1="40" y1="185" x2="220" y2="185" strokeWidth="6" />
            <line x1="40" y1="245" x2="190" y2="245" strokeWidth="10" />
            <line x1="40" y1="285" x2="370" y2="285" strokeWidth="6" />
            <line x1="40" y1="310" x2="350" y2="310" strokeWidth="6" />
            <line x1="40" y1="335" x2="290" y2="335" strokeWidth="6" />
            <line x1="40" y1="380" x2="40" y2="430" strokeWidth="4" />
            <line x1="60" y1="390" x2="320" y2="390" strokeWidth="6" />
            <line x1="60" y1="415" x2="240" y2="415" strokeWidth="6" />
          </g>
          <line x1="40" y1="40" x2="260" y2="40" className="auth-draw-line auth-doc-draw-1" style={{ stroke: "#3a312b", strokeWidth: 14 }} />
          <line x1="40" y1="65" x2="140" y2="65" className="auth-draw-line auth-doc-draw-2" style={{ stroke: "#d2a878", strokeWidth: 6 }} />
          <line x1="40" y1="110" x2="360" y2="110" className="auth-draw-line auth-doc-draw-3" style={{ stroke: "#5c4c3c", strokeWidth: 6 }} />
          <line x1="40" y1="135" x2="380" y2="135" className="auth-draw-line auth-doc-draw-4" style={{ stroke: "#5c4c3c", strokeWidth: 6 }} />
          <line x1="40" y1="160" x2="330" y2="160" className="auth-draw-line auth-doc-draw-5" style={{ stroke: "#5c4c3c", strokeWidth: 6 }} />
          <line x1="40" y1="185" x2="220" y2="185" className="auth-draw-line auth-doc-draw-6" style={{ stroke: "#5c4c3c", strokeWidth: 6 }} />
          <line x1="40" y1="245" x2="190" y2="245" className="auth-draw-line auth-doc-draw-7" style={{ stroke: "#3a312b", strokeWidth: 10 }} />
          <line x1="40" y1="285" x2="370" y2="285" className="auth-draw-line auth-doc-draw-8" style={{ stroke: "#5c4c3c", strokeWidth: 6 }} />
          <line x1="40" y1="310" x2="350" y2="310" className="auth-draw-line auth-doc-draw-9" style={{ stroke: "#5c4c3c", strokeWidth: 6 }} />
          <line x1="40" y1="335" x2="290" y2="335" className="auth-draw-line auth-doc-draw-10" style={{ stroke: "#5c4c3c", strokeWidth: 6 }} />
          <line x1="40" y1="380" x2="40" y2="430" className="auth-draw-line auth-doc-draw-11" style={{ stroke: "#d2a878", strokeWidth: 4 }} />
          <line x1="60" y1="390" x2="320" y2="390" className="auth-draw-line auth-doc-draw-11" style={{ stroke: "#708f73", strokeWidth: 6 }} />
          <line x1="60" y1="415" x2="240" y2="415" className="auth-draw-line auth-doc-draw-12" style={{ stroke: "#708f73", strokeWidth: 6 }} />
        </svg>
      </div>
    </div>
  );
}

function ProductShowcase() {
  return (
    <div className="auth-perspective auth-bg-mesh relative hidden flex-1 items-center justify-center overflow-hidden lg:flex">
      <KnowledgeBackground />

      <div className="auth-scene relative flex h-[700px] w-[900px] items-center justify-center">
        <ChatShowcase />
        <DocumentShowcase />

        <div
          className="auth-float-fast absolute bottom-20 right-0 z-30 flex items-center gap-3 rounded-full border border-white bg-white/70 px-5 py-3 shadow-xl backdrop-blur-xl"
          style={{ transform: "translateZ(120px) translateX(20px)" }}
        >
          <PenTool className="h-5 w-5 text-[#5c4c3c]" />
          <div>
            <div className="mb-1.5 h-1.5 w-12 rounded-full bg-[#5c4c3c]" />
            <div className="h-1 w-8 rounded-full bg-[#decbae]" />
          </div>
        </div>

        <div
          className="auth-float-slow absolute -right-8 top-1/4 z-30 flex h-16 w-16 items-center justify-center rounded-full border-2 border-white bg-[#3a312b] shadow-[0_10px_30px_rgba(58,49,43,0.3)]"
          style={{ transform: "translateZ(80px)" }}
        >
          <WandSparkles className="h-6 w-6 text-[#ebe2d2]" />
        </div>
      </div>
    </div>
  );
}

export function AuthPanel({ initialMode }: AuthPanelProps) {
  const [mode, setMode] = useState(initialMode);
  const [accountIdentifier, setAccountIdentifier] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [emailChallengeId, setEmailChallengeId] = useState<string | null>(null);
  const [registrationChallengeId, setRegistrationChallengeId] = useState<string | null>(null);
  const [useEmailCode, setUseEmailCode] = useState(false);
  const [currentUser, setCurrentUser] = useState<UserView | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isCheckingSession, setIsCheckingSession] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [turnstileResetKey, setTurnstileResetKey] = useState(0);
  const [authProviders, setAuthProviders] = useState<AuthProviderView[]>([]);
  const [codexLogin, setCodexLogin] = useState<CodexLoginStartResponse | null>(null);
  const [codexLoginStatus, setCodexLoginStatus] = useState<string | null>(null);
  const loginSearch = useSyncExternalStore(subscribeToLocationSearch, getLocationSearch, getServerLocationSearch);
  const codexCompletionInFlightRef = useRef(false);
  const chatGPTPlatformProvider = authProviders.find(
    (provider) => provider.id === "chatgpt" && provider.kind === "device"
  );

  const completeCodexPlatformLoginOnce = useCallback(async (loginId?: string) => {
    if (codexCompletionInFlightRef.current) {
      return false;
    }
    codexCompletionInFlightRef.current = true;
    try {
      const payload = await api.completeCodexPlatformLogin(loginId);
      storeAuthToken(payload.token);
      clearPendingCodexLogin();
      setCodexLogin(null);
      setCodexLoginStatus("succeeded");
      const nextPath = new URLSearchParams(window.location.search).get("next");
      navigateAfterAuth(loginDestination(nextPath), "replace");
      return true;
    } catch (completionError) {
      codexCompletionInFlightRef.current = false;
      throw completionError;
    }
  }, []);

  useEffect(() => {
    let disposed = false;

    async function loadUser() {
      try {
        const [user, providers] = await Promise.all([
          api.getCurrentUser().catch(() => null),
          loadAuthProvidersWithRetry(),
        ]);
        if (!disposed) {
          setCurrentUser(user?.role === "guest" ? null : user);
          setAuthProviders(providers);
          if (user && user.role !== "guest") {
            storeAuthToken(readAuthToken());
          }
        }
      } catch {
        if (!disposed) {
          setCurrentUser(null);
          setAuthProviders([]);
          setError("Could not connect to the sign-in service. Refresh the page and try again.");
        }
      } finally {
        if (!disposed) {
          setIsCheckingSession(false);
        }
      }
    }

    void loadUser();

    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (!currentUser) {
      return;
    }
    const nextPath = new URLSearchParams(window.location.search).get("next");
    navigateAfterAuth(loginDestination(nextPath), "replace");
  }, [currentUser]);

  useEffect(() => {
    if (loginSearch === null) {
      return;
    }
    if (isCheckingSession) {
      return;
    }
    if (!chatGPTPlatformProvider?.configured) {
      clearPendingCodexLogin();
      return;
    }
    if (!readGuestAuthToken()) {
      clearPendingCodexLogin();
      return;
    }
    const pendingLogin = readPendingCodexLogin();
    if (pendingLogin) {
      const restoreTimeoutId = window.setTimeout(() => {
        setCodexLogin(pendingLogin);
        setCodexLoginStatus("pending");
      }, 0);
      return () => window.clearTimeout(restoreTimeoutId);
    }
    let disposed = false;

    async function resumeCompletedChatGPTLogin() {
      try {
        const provider = await api.getCodexStatus();
        if (!disposed && provider.configured) {
          await completeCodexPlatformLoginOnce();
        }
      } catch {
        // A guest session without a completed ChatGPT login remains on this page.
      }
    }

    void resumeCompletedChatGPTLogin();
    return () => {
      disposed = true;
    };
  }, [chatGPTPlatformProvider?.configured, completeCodexPlatformLoginOnce, isCheckingSession, loginSearch]);

  useEffect(() => {
    if (!codexLogin) {
      return;
    }
    const activeCodexLogin = codexLogin;
    let disposed = false;
    let timeoutId: number | null = null;

    function scheduleNextPoll() {
      if (!disposed) {
        timeoutId = window.setTimeout(() => void refreshLoginStatus(), CODEX_LOGIN_POLL_INTERVAL_MS);
      }
    }

    async function refreshLoginStatus() {
      if (pendingCodexLoginExpired(activeCodexLogin)) {
        clearPendingCodexLogin();
        setCodexLogin(null);
        setCodexLoginStatus("expired");
        setError("ChatGPT sign-in expired. Please start again.");
        return;
      }
      let status;
      try {
        status = await api.getCodexLoginStatus(activeCodexLogin.login_id);
      } catch (statusError) {
        if (disposed) {
          return;
        }
        try {
          const provider = await api.getCodexStatus();
          if (disposed) {
            return;
          }
          if (provider.configured) {
            const completed = await completeCodexPlatformLoginOnce();
            if (!completed) {
              scheduleNextPoll();
            }
            return;
          }
        } catch {
          // Keep the pending marker and retry after transient status failures.
        }
        setError(statusError instanceof Error ? statusError.message : "Could not check ChatGPT sign-in status");
        scheduleNextPoll();
        return;
      }
      if (disposed) {
        return;
      }
      if (status.status === "succeeded") {
        try {
          const completed = await completeCodexPlatformLoginOnce(activeCodexLogin.login_id);
          if (!completed) {
            scheduleNextPoll();
          }
        } catch (completionError) {
          setError(completionError instanceof Error ? completionError.message : "Could not complete ChatGPT sign-in");
          scheduleNextPoll();
        }
        return;
      }
      setCodexLoginStatus(status.status);
      if (["failed", "cancelled", "expired"].includes(status.status)) {
        clearPendingCodexLogin();
        setCodexLogin(null);
        setError(status.error || "ChatGPT sign-in was not completed");
        return;
      }
      setError(null);
      scheduleNextPoll();
    }

    void refreshLoginStatus();
    return () => {
      disposed = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [codexLogin, completeCodexPlatformLoginOnce]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    setNotice(null);

    try {
      const payload = await api.login(accountIdentifier, password, turnstileToken);
      storeAuthToken(payload.token);
      setCurrentUser(payload.user);
      const nextPath = new URLSearchParams(window.location.search).get("next");
      navigateAfterAuth(loginDestination(nextPath));
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Operation failed");
      setTurnstileResetKey((value) => value + 1);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleRegistrationCodeRequest() {
    if (!turnstileSubmissionReady(turnstileToken)) {
      setError("Complete human verification before requesting an email code.");
      setNotice(null);
      document
        .querySelector<HTMLElement>("[aria-label=\"Cloudflare Turnstile human verification\"]")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    setIsLoading(true);
    setError(null);
    setNotice(null);
    setEmailCode("");

    try {
      const response = await api.requestRegistrationEmailCode(accountIdentifier, turnstileToken);
      setRegistrationChallengeId(response.challenge_id);
      setNotice(response.message);
      setTurnstileResetKey((value) => value + 1);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Could not send the verification code");
      setTurnstileResetKey((value) => value + 1);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleRegistrationSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    setNotice(null);

    try {
      if (password !== passwordConfirmation) {
        setError("The passwords do not match.");
        return;
      }
      if (!registrationChallengeId) {
        setError("Request and enter an email verification code first.");
        return;
      }
      const payload = await api.registerEmail({
        email: accountIdentifier,
        username,
        password,
        password_confirmation: passwordConfirmation,
        challenge_id: registrationChallengeId,
        code: emailCode,
        ...(turnstileToken ? { turnstile_token: turnstileToken } : {}),
      });
      storeAuthToken(payload.token);
      setCurrentUser(payload.user);
      const nextPath = new URLSearchParams(window.location.search).get("next");
      navigateAfterAuth(loginDestination(nextPath));
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Registration failed");
      setTurnstileResetKey((value) => value + 1);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleEmailCodeSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    setNotice(null);

    try {
      if (!emailChallengeId) {
        const response = await api.requestEmailCode(accountIdentifier, turnstileToken);
        setEmailChallengeId(response.challenge_id);
        setNotice(response.message);
        setTurnstileResetKey((value) => value + 1);
        return;
      }
      const payload = await api.verifyEmailCode(emailChallengeId, emailCode, turnstileToken);
      storeAuthToken(payload.token);
      setCurrentUser(payload.user);
      const nextPath = new URLSearchParams(window.location.search).get("next");
      navigateAfterAuth(loginDestination(nextPath));
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Operation failed");
      setTurnstileResetKey((value) => value + 1);
    } finally {
      setIsLoading(false);
    }
  }

  function resetEmailCodeFlow() {
    setUseEmailCode(false);
    setEmailChallengeId(null);
    setRegistrationChallengeId(null);
    setEmailCode("");
    setTurnstileToken(null);
    setTurnstileResetKey((value) => value + 1);
  }

  function handleAuthModeChange(nextMode: "register" | "login") {
    setMode(nextMode);
    setError(null);
    setNotice(null);
    resetEmailCodeFlow();
  }

  function handleLogout() {
    clearAuthToken();
    setCurrentUser(null);
  }

  async function handleGuestAccess() {
    setIsLoading(true);
    setError(null);
    setNotice(null);
    try {
      const payload = await api.startGuestSession();
      if (!payload.token) {
        throw new Error("Guest session did not return a valid token");
      }
      storeGuestAuthToken(payload.token);
      navigateAfterAuth("/studio");
    } catch (guestError) {
      setError(guestError instanceof Error ? guestError.message : "Could not start a guest session");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleChatGPTLogin() {
    if (!chatGPTPlatformProvider?.configured) {
      setError("ChatGPT sign-in is currently unavailable");
      return;
    }
    setIsLoading(true);
    setError(null);
    setNotice(null);
    try {
      if (!readGuestAuthToken()) {
        const guest = await api.startGuestSession();
        if (!guest.token) {
          throw new Error("Guest session did not return a valid token");
        }
        storeGuestAuthToken(guest.token);
      }
      const login = await api.startChatGPTPlatformLogin();
      storePendingCodexLogin(login);
      setCodexLogin(login);
      setCodexLoginStatus("pending");
      window.open(login.verification_url, "_blank", "noopener,noreferrer");
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Could not start ChatGPT sign-in");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleCancelChatGPTLogin() {
    if (!codexLogin) {
      return;
    }
    const loginId = codexLogin.login_id;
    clearPendingCodexLogin();
    setCodexLogin(null);
    setCodexLoginStatus("cancelled");
    setIsLoading(true);
    try {
      await api.cancelCodexLogin(loginId);
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Could not cancel ChatGPT sign-in");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleHomeAccess() {
    if (currentUser) {
      navigateAfterAuth(loginDestination("/"));
      return;
    }
    await handleGuestAccess();
  }

  function handleProviderSignIn(option: SocialSignInOption) {
    setError(null);
    const provider = authProviders.find((item) => item.id === option.id);
    if (!provider?.configured) {
      setNotice(`${option.providerLabel} requires an OAuth Client/App ID and Secret in the server .env. You can also register or sign in with email.`);
      return;
    }
    const nextPath =
      typeof window !== "undefined" ? loginRedirectPath(new URLSearchParams(window.location.search).get("next")) : "/";
    const params = new URLSearchParams({ next: nextPath });
    window.location.assign(`${getApiBase()}/api/auth/oauth/${option.id}/start?${params.toString()}`);
  }

  const isRegister = mode === "register";
  const alternateMode = isRegister ? "login" : "register";
  const authModeSearch = loginSearch ?? "";
  const isChatGPTLoginPending = Boolean(codexLogin && codexLoginStatus === "pending");
  const isAuthBusy = isLoading || isChatGPTLoginPending;

  return (
    <main className="auth-shell min-h-screen overflow-hidden bg-[#fcfbf9] text-[#3a312b]">
      <div className="flex min-h-screen flex-col lg:flex-row">
        <section className="relative z-20 flex w-full flex-col justify-center border-[#f0e8dc] bg-[#fcfbf9] px-6 py-8 shadow-[20px_0_40px_-15px_rgba(58,49,43,0.06)] sm:px-12 sm:py-10 lg:w-[45%] lg:border-r lg:px-16 lg:py-0 xl:w-[40%] xl:px-24">
          <div className="w-full max-w-[21rem] sm:mx-auto sm:max-w-[28rem]">
            <div className="mb-6 flex items-center justify-between gap-4 sm:mb-8">
              <button
                type="button"
                onClick={() => void handleHomeAccess()}
                disabled={isAuthBusy}
                className="flex min-w-0 items-center gap-3 text-left disabled:cursor-wait disabled:opacity-70"
                aria-label="Go to product homepage"
              >
                <BrandMark
                  alt=""
                  className="h-10 w-10 rounded-lg border border-[#ebe2d2] bg-white shadow-lg shadow-[#3a312b]/10"
                  priority
                  size={80}
                />
                <span className="auth-display truncate text-2xl font-bold text-[#3a312b]">OpenClass</span>
              </button>
              <button
                type="button"
                onClick={() => void handleHomeAccess()}
                disabled={isAuthBusy}
                className="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg border border-[#ebe2d2] bg-white px-3 text-sm font-semibold text-[#5c4c3c] transition hover:border-[#d2a878] hover:text-[#3a312b] disabled:cursor-wait disabled:opacity-70"
              >
                <ArrowLeft className="h-4 w-4" />

                Home
              </button>
            </div>

            <div className="mb-6 sm:mb-7">
              <h1 className="auth-display text-4xl font-bold leading-[1.08] text-[#3a312b] sm:text-5xl">

                Structure ideas
                <br />
                <span className="bg-gradient-to-r from-[#ead9b3] via-[#d2b77c] to-[#a78651] bg-clip-text text-transparent">

                  Create with clarity
                </span>
              </h1>
              <p className="mt-4 text-base leading-7 text-[#5c4c3c]/70">

                Turn ideas into structured courses, clear lessons, and reusable learning resources. Sign in to keep creating.
              </p>
            </div>

            {isCheckingSession ? (
              <div className="flex min-h-80 items-center justify-center rounded-lg border border-[#ebe2d2] bg-white/80 text-sm font-medium text-[#5c4c3c]">
                <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />

                Checking sign-in status
              </div>
            ) : currentUser ? (
              <div className="rounded-lg border border-[#ebe2d2] bg-white p-6 shadow-[0_18px_48px_rgba(58,49,43,0.08)]">
                <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-[#3a312b] text-white">
                  <ShieldCheck className="h-5 w-5" />
                </div>
                <h2 className="auth-display mt-5 text-2xl font-bold text-[#3a312b]">You’re signed in</h2>
                <p className="mt-2 break-all text-sm text-[#5c4c3c]">{userAccountLabel(currentUser)}</p>
                <p className="mt-1 text-xs text-[#8d8377]">Role: {currentUser.role === "admin" ? "Administrator" : "Member"}</p>
                <div className="mt-6 flex flex-col gap-2 sm:flex-row">
                  <Link
                    href={currentUser.role === "admin" ? "/admin" : "/"}
                    className="inline-flex h-11 items-center justify-center rounded-lg bg-[#3a312b] px-4 text-sm font-bold text-white transition hover:bg-[#1f1a17]"
                  >
                    {currentUser.role === "admin" ? "Open admin dashboard" : "Return home"}
                  </Link>
                  <button
                    type="button"
                    onClick={handleLogout}
                    className="inline-flex h-11 items-center justify-center rounded-lg border border-[#ebe2d2] bg-white px-4 text-sm font-bold text-[#5c4c3c] transition hover:border-[#d2a878] hover:text-[#3a312b]"
                  >

                    Log out
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="mb-5 space-y-3">
                  {chatGPTPlatformProvider ? (
                    <button
                      type="button"
                      onClick={() => void handleChatGPTLogin()}
                      disabled={isAuthBusy || !chatGPTPlatformProvider.configured}
                      className="flex w-full items-center justify-center gap-3 rounded-lg border border-[#3a312b] bg-[#3a312b] px-4 py-3.5 text-sm font-semibold text-white shadow-sm transition hover:bg-[#1f1a17] active:scale-[0.99] disabled:cursor-wait disabled:opacity-70"
                    >
                      {isLoading ? (
                        <LoaderCircle className="h-4 w-4 animate-spin" />
                      ) : (
                        <Image src="/chatgpt-logo.png" alt="" width={20} height={20} className="h-5 w-5 rounded-sm" />
                      )}
                      <span className="whitespace-nowrap">Continue with ChatGPT</span>
                      {!chatGPTPlatformProvider.configured ? (
                        <span className="shrink-0 rounded-full bg-white/20 px-2 py-0.5 text-[11px] font-semibold">Not available yet</span>
                      ) : null}
                    </button>
                  ) : null}
                  {socialSignInOptions.map((option) => {
                    const provider = authProviders.find((item) => item.kind === "oauth" && item.id === option.id);
                    const isUnconfigured = provider ? !provider.configured : false;
                    const statusClassName =
                      option.brand === "google" || option.brand === "microsoft" ? "bg-black/5 text-[#5c4c3c]" : "bg-white/20 text-current";

                    return (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() => handleProviderSignIn(option)}
                        disabled={isAuthBusy}
                        className={clsx(
                          "flex w-full items-center justify-center gap-3 rounded-lg border px-4 py-3.5 text-sm font-semibold shadow-sm transition active:scale-[0.99] disabled:cursor-wait disabled:opacity-70",
                          option.className
                        )}
                      >
                        <SocialBrandIcon brand={option.brand} />
                        <span className="whitespace-nowrap">{option.label}</span>
                        {isUnconfigured ? (
                          <span className={clsx("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold", statusClassName)}>Not configured</span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>

                {codexLogin ? (
                  <div className="mb-5 rounded-lg border border-[#b9cbb8] bg-[#f1f7ef] px-3 py-3 text-sm leading-6 text-[#496a4c]">
                    <p className="font-semibold">Complete sign-in on the ChatGPT page</p>
                    <p className="mt-1">Enter device code: <span className="font-mono font-bold">{codexLogin.user_code}</span></p>
                    <div className="mt-2 flex items-center gap-3">
                      <a
                        href={codexLogin.verification_url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-semibold text-[#3a312b] underline underline-offset-2"
                      >

                        Open ChatGPT
                      </a>
                      <button
                        type="button"
                        onClick={() => void handleCancelChatGPTLogin()}
                        disabled={isLoading}
                        className="font-semibold text-[#8d8377] underline underline-offset-2 disabled:opacity-60"
                      >

                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}

                {notice ? (
                  <div className="mb-5 rounded-lg border border-[#b9cbb8] bg-[#f1f7ef] px-3 py-2 text-sm leading-6 text-[#496a4c]">
                    {notice}
                  </div>
                ) : null}

                <div className="mb-5 flex items-center gap-3 text-xs font-semibold text-[#8d8377]">
                  <span className="h-px flex-1 bg-[#ebe2d2]" />
                  {isRegister ? "Create an account with email" : "Or continue with email or phone"}
                  <span className="h-px flex-1 bg-[#ebe2d2]" />
                </div>

                <div className="mb-4 grid grid-cols-2 gap-2 rounded-lg bg-[#f7f3eb] p-1">
                  {(["login", "register"] as const).map((item) => (
                    <Link
                      key={item}
                      href={`/${item}${authModeSearch}`}
                      onClick={() => handleAuthModeChange(item)}
                      className={clsx(
                        "flex h-10 items-center justify-center rounded-lg text-sm font-bold transition",
                        mode === item ? "bg-white text-[#3a312b] shadow-sm" : "text-[#8d8377] hover:text-[#3a312b]"
                      )}
                    >
                      {item === "register" ? "Create account" : "Sign in"}
                    </Link>
                  ))}
                </div>

                {!isRegister ? (
                  <div className="mb-4 grid grid-cols-2 gap-2 rounded-lg border border-[#ebe2d2] bg-white p-1">
                    <button
                      type="button"
                      onClick={() => {
                        resetEmailCodeFlow();
                        setError(null);
                        setNotice(null);
                      }}
                      className={clsx(
                        "h-9 rounded-lg text-sm font-semibold transition",
                        !useEmailCode ? "bg-[#f7f3eb] text-[#3a312b]" : "text-[#8d8377] hover:text-[#3a312b]"
                      )}
                    >

                      Password
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setUseEmailCode(true);
                        setEmailChallengeId(null);
                        setEmailCode("");
                        setError(null);
                        setNotice(null);
                      }}
                      className={clsx(
                        "h-9 rounded-lg text-sm font-semibold transition",
                        useEmailCode ? "bg-[#f7f3eb] text-[#3a312b]" : "text-[#8d8377] hover:text-[#3a312b]"
                      )}
                    >

                      Email code
                    </button>
                  </div>
                ) : null}

                <form
                  onSubmit={(event) =>
                    void (
                      isRegister
                        ? handleRegistrationSubmit(event)
                        : useEmailCode
                          ? handleEmailCodeSubmit(event)
                          : handleSubmit(event)
                    )
                  }
                  className="space-y-4"
                >
                  <AuthInput
                    id="account"
                    label={isRegister || useEmailCode ? "Email" : "Email or phone number"}
                    type={isRegister || useEmailCode ? "email" : "text"}
                    value={accountIdentifier}
                    onChange={(value) => {
                      setAccountIdentifier(value);
                      if (emailChallengeId) {
                        setEmailChallengeId(null);
                        setEmailCode("");
                        setNotice(null);
                      }
                      if (registrationChallengeId) {
                        setRegistrationChallengeId(null);
                        setEmailCode("");
                        setNotice(null);
                      }
                    }}
                    autoComplete={isRegister || useEmailCode ? "email" : "username"}
                    placeholder={isRegister || useEmailCode ? "name@company.com" : "name@company.com / 13800138000"}
                    Icon={isRegister || useEmailCode ? Mail : User}
                  />

                  {isRegister ? (
                    <AuthInput
                      id="username"
                      label="Username"
                      type="text"
                      value={username}
                      onChange={setUsername}
                      minLength={2}
                      autoComplete="nickname"
                      placeholder="Display username"
                      Icon={User}
                    />
                  ) : null}

                  {!isRegister && useEmailCode && emailChallengeId ? (
                    <AuthInput
                      id="email-code"
                      label="6-digit verification code"
                      type="text"
                      value={emailCode}
                      onChange={setEmailCode}
                      inputMode="numeric"
                      maxLength={6}
                      minLength={6}
                      pattern="[0-9]{6}"
                      autoComplete="one-time-code"
                      placeholder="000000"
                      Icon={LockKeyhole}
                    />
                  ) : null}

                  {!useEmailCode || isRegister ? (
                    <div className="space-y-4">
                      {!isRegister ? (
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-sm font-semibold text-[#5c4c3c]">Password</span>
                          <Link
                            href="/forgot-password"
                            className="text-sm font-medium text-[#b88952] transition hover:text-[#5c4c3c]"
                          >

                            Forgot password?
                          </Link>
                        </div>
                      ) : null}
                      <AuthInput
                        id="password"
                        label={isRegister ? "Password" : ""}
                        type="password"
                        value={password}
                        onChange={setPassword}
                        minLength={8}
                        autoComplete={isRegister ? "new-password" : "current-password"}
                        placeholder={isRegister ? "At least 8 characters" : "••••••••"}
                        Icon={LockKeyhole}
                      />
                      {isRegister ? (
                        <AuthInput
                          id="password-confirmation"
                          label="Confirm Password"
                          type="password"
                          value={passwordConfirmation}
                          onChange={setPasswordConfirmation}
                          minLength={8}
                          autoComplete="new-password"
                          placeholder="Re-enter your password"
                          Icon={ShieldCheck}
                        />
                      ) : null}
                    </div>
                  ) : null}

                  {isRegister ? (
                    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2">
                      <AuthInput
                        id="registration-email-code"
                        label="Email verification code"
                        type="text"
                        value={emailCode}
                        onChange={setEmailCode}
                        inputMode="numeric"
                        maxLength={6}
                        minLength={6}
                        pattern="[0-9]{6}"
                        autoComplete="one-time-code"
                        placeholder="6-digit verification code"
                        Icon={LockKeyhole}
                      />
                      <button
                        type="button"
                        onClick={() => void handleRegistrationCodeRequest()}
                        disabled={isAuthBusy}
                        className="h-[50px] whitespace-nowrap rounded-lg border border-[#d2a878] bg-white px-4 text-sm font-semibold text-[#5c4c3c] shadow-sm transition hover:bg-[#f7f3eb] disabled:cursor-wait disabled:opacity-60"
                      >
                        {registrationChallengeId ? "Resend" : "Send verification code"}
                      </button>
                    </div>
                  ) : null}

                  <TurnstileWidget
                    action={isRegister ? "register" : useEmailCode ? "email_code_login" : "login"}
                    onTokenChange={setTurnstileToken}
                    resetKey={turnstileResetKey}
                  />

                  {error ? (
                    <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-700">{error}</div>
                  ) : null}

                  <button
                    type="submit"
                    disabled={isAuthBusy || !turnstileSubmissionReady(turnstileToken)}
                    className="flex w-full items-center justify-center gap-2 rounded-lg border border-transparent bg-[#3a312b] px-4 py-3 text-sm font-bold text-white shadow-[0_8px_20px_-8px_rgba(58,49,43,0.5)] transition hover:bg-[#1f1a17] focus:outline-none focus:ring-2 focus:ring-[#3a312b] focus:ring-offset-2 disabled:cursor-wait disabled:opacity-70 sm:py-3.5"
                  >
                    {isLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <GraduationCap className="h-4 w-4" />}
                    {isRegister ? "Create account" : useEmailCode ? (emailChallengeId ? "Verify and sign in" : "Send verification code") : "Open Studio"}
                  </button>
                </form>

                <p className="mt-5 text-center text-sm text-[#5c4c3c]/70">
                  {isRegister ? "Already have an account?" : "Don’t have an account yet?"}
                  <Link
                    href={`/${alternateMode}${authModeSearch}`}
                    onClick={() => handleAuthModeChange(alternateMode)}
                    className="ml-1 border-b border-[#3a312b] pb-0.5 font-semibold text-[#3a312b] transition hover:border-[#d2a878] hover:text-[#b88952]"
                  >
                    {isRegister ? "Return to sign in" : "Create account"}
                  </Link>
                </p>

                {!isRegister ? (
                  <button
                    type="button"
                    onClick={() => void handleGuestAccess()}
                    disabled={isAuthBusy}
                    className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-[#ebe2d2] bg-white px-4 py-3 text-sm font-bold text-[#5c4c3c] shadow-sm transition hover:border-[#d2a878] hover:text-[#3a312b] disabled:cursor-wait disabled:opacity-70"
                  >
                    {isLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}

                    Continue as guest (activity is not saved)
                  </button>
                ) : null}

                <p className="mt-5 text-center text-xs leading-5 text-[#5c4c3c]/60">

                  By continuing, you agree to the
                  <Link href="/terms" className="mx-1 underline transition hover:text-[#3a312b]">Terms of Service</Link>

                  and
                  <Link href="/privacy" className="mx-1 underline transition hover:text-[#3a312b]">Privacy Policy</Link>.
                </p>
              </>
            )}
          </div>
        </section>

        <ProductShowcase />
      </div>
    </main>
  );
}
