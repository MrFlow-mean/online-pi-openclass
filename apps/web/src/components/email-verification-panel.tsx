"use client";

import { useState, type FormEvent } from "react";
import { LoaderCircle, MailCheck } from "lucide-react";

import { AuthFormShell } from "@/components/auth-form-shell";
import { TurnstileWidget, turnstileSubmissionReady } from "@/components/turnstile-widget";
import { api } from "@/lib/api";

export function EmailVerificationPanel() {
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function requestCode() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.requestEmailVerification(token);
      setChallengeId(result.challenge_id);
      setMessage(result.message);
      setResetKey((value) => value + 1);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法发送验证邮件");
      setResetKey((value) => value + 1);
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!challengeId) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.confirmEmailVerification(challengeId, code, token);
      setMessage("邮箱验证完成。");
      setChallengeId(null);
      setCode("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "邮箱验证失败");
      setResetKey((value) => value + 1);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFormShell title="验证邮箱" description="验证主邮箱后，可用于找回密码和接收账户安全通知。">
      <form className="space-y-5" onSubmit={(event) => void confirm(event)}>
        <div className="flex items-start gap-3 rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm leading-6 text-stone-600">
          <MailCheck className="mt-0.5 h-5 w-5 shrink-0 text-stone-800" />
          验证码只发送到当前登录账号的主邮箱，并会在短时间后失效。
        </div>
        {challengeId ? (
          <label className="block text-sm font-semibold text-stone-800">
            6 位验证码
            <input required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value)} className="mt-2 w-full rounded-lg border border-stone-300 px-3 py-2.5 outline-none focus:border-stone-700" />
          </label>
        ) : null}
        <TurnstileWidget action={challengeId ? "email_verification_confirm" : "email_verification_request"} onTokenChange={setToken} resetKey={resetKey} />
        {message ? <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</p> : null}
        {error ? <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p> : null}
        {challengeId ? (
          <button disabled={busy || !turnstileSubmissionReady(token)} className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-stone-950 text-sm font-semibold text-white disabled:opacity-50">
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}确认邮箱
          </button>
        ) : (
          <button type="button" onClick={() => void requestCode()} disabled={busy || !turnstileSubmissionReady(token)} className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-stone-950 text-sm font-semibold text-white disabled:opacity-50">
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}发送验证码
          </button>
        )}
      </form>
    </AuthFormShell>
  );
}
