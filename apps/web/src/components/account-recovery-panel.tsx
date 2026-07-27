"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { LoaderCircle } from "lucide-react";

import { AuthFormShell } from "@/components/auth-form-shell";
import { TurnstileWidget, turnstileSubmissionReady } from "@/components/turnstile-widget";
import { api } from "@/lib/api";

const inputClass =
  "mt-2 w-full rounded-lg border border-stone-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-stone-700 focus:ring-2 focus:ring-stone-100";

export function ForgotPasswordPanel() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.requestPasswordReset(email, token);
      const params = new URLSearchParams({ challenge: result.challenge_id, email });
      router.push(`/reset-password?${params.toString()}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法发送重置验证码");
      setResetKey((value) => value + 1);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFormShell title="找回密码" description="输入注册邮箱。若账号存在，我们会发送一次性验证码；提示信息不会泄露邮箱是否已注册。">
      <form className="space-y-5" onSubmit={(event) => void submit(event)}>
        <label className="block text-sm font-semibold text-stone-800">
          注册邮箱
          <input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} className={inputClass} />
        </label>
        <TurnstileWidget action="password_forgot" onTokenChange={setToken} resetKey={resetKey} />
        {error ? <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p> : null}
        <button disabled={busy || !turnstileSubmissionReady(token)} className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-stone-950 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
          {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
          发送验证码
        </button>
      </form>
    </AuthFormShell>
  );
}

export function ResetPasswordPanel() {
  const searchParams = useSearchParams();
  const challengeId = searchParams.get("challenge") || "";
  const email = searchParams.get("email") || "";
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!challengeId) {
      setError("重置请求缺少 challenge_id，请重新发送验证码。");
      return;
    }
    if (password !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.resetPassword({
        challenge_id: challengeId,
        code,
        password,
        password_confirmation: confirmation,
        turnstile_token: token,
      });
      setMessage(result.message || "密码已更新，请使用新密码登录。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "密码重置失败");
      setResetKey((value) => value + 1);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFormShell title="设置新密码" description={email ? `验证码已发送至 ${email}。` : "填写邮件中的验证码和新密码。"}>
      {message ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-800">{message}</div>
      ) : (
        <form className="space-y-5" onSubmit={(event) => void submit(event)}>
          <label className="block text-sm font-semibold text-stone-800">6 位验证码<input required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value)} className={inputClass} /></label>
          <label className="block text-sm font-semibold text-stone-800">新密码<input required minLength={8} type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} className={inputClass} /></label>
          <label className="block text-sm font-semibold text-stone-800">确认新密码<input required minLength={8} type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} className={inputClass} /></label>
          <TurnstileWidget action="password_reset" onTokenChange={setToken} resetKey={resetKey} />
          {error ? <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p> : null}
          <button disabled={busy || !turnstileSubmissionReady(token)} className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-stone-950 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
            更新密码
          </button>
        </form>
      )}
    </AuthFormShell>
  );
}
