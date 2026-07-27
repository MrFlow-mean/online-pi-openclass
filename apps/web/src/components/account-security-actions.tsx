"use client";

import { useState, type FormEvent } from "react";
import { Download, LoaderCircle, LogOut, Trash2 } from "lucide-react";

import { api, clearAuthToken, storeAuthToken } from "@/lib/api";
import type { InterfaceLanguage } from "@/lib/profile-settings-state";
import type { UserView } from "@/types";

const inputClass = "mt-2 w-full max-w-xl rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-900 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100";

export function AccountSecurityActions({ language, onSignedOut, user }: { language: InterfaceLanguage; onSignedOut: () => void; user: UserView }) {
  const zh = language === "zh-CN";
  const hasPasswordIdentity = user.auth_identities.some((identity) => identity.provider === "email" || identity.provider === "phone");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");

  async function run(action: string, task: () => Promise<void>) {
    setBusyAction(action);
    setMessage(null);
    setError(null);
    try {
      await task();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : zh ? "操作失败，请稍后重试。" : "The action failed. Please try again.");
    } finally {
      setBusyAction(null);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const currentPassword = String(data.get("currentPassword") || "");
    const newPassword = String(data.get("newPassword") || "");
    const confirmation = String(data.get("confirmPassword") || "");
    if (newPassword.length < 8) {
      setError(zh ? "新密码至少需要 8 位。" : "The new password must have at least 8 characters.");
      return;
    }
    if (newPassword !== confirmation) {
      setError(zh ? "两次输入的新密码不一致。" : "The new passwords do not match.");
      return;
    }
    await run("password", async () => {
      const result = await api.changePassword(currentPassword, newPassword, confirmation);
      storeAuthToken(result.token);
      form.reset();
      setMessage(zh ? "密码已更新。其他会话已失效。" : "Password updated. Other sessions were revoked.");
    });
  }

  async function revokeSessions() {
    await run("sessions", async () => {
      await api.revokeAllSessions();
      clearAuthToken();
      onSignedOut();
    });
  }

  async function exportData() {
    await run("export", async () => {
      const blob = await api.exportAccountData();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `openclass-account-export-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage(zh ? "数据导出已开始下载。" : "Your data export is downloading.");
    });
  }

  async function deleteAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (deleteConfirmation !== "DELETE") {
      setError(zh ? "请输入 DELETE 确认注销。" : "Type DELETE to confirm account deletion.");
      return;
    }
    await run("delete", async () => {
      await api.deleteAccount(deletePassword);
      clearAuthToken();
      onSignedOut();
    });
  }

  const busy = busyAction !== null;
  return <div className="max-w-3xl space-y-8">
    {hasPasswordIdentity ? <form className="space-y-5" onSubmit={(event) => void changePassword(event)}>
      <h3 className="text-base font-semibold text-stone-950">{zh ? "修改密码" : "Change password"}</h3>
      <label className="block text-sm font-semibold text-stone-950">{zh ? "当前密码" : "Current password"}<input required className={inputClass} type="password" name="currentPassword" autoComplete="current-password" /></label>
      <label className="block text-sm font-semibold text-stone-950">{zh ? "新密码" : "New password"}<input required minLength={8} className={inputClass} type="password" name="newPassword" autoComplete="new-password" /></label>
      <label className="block text-sm font-semibold text-stone-950">{zh ? "确认新密码" : "Confirm new password"}<input required minLength={8} className={inputClass} type="password" name="confirmPassword" autoComplete="new-password" /></label>
      <button disabled={busy} className="inline-flex h-10 items-center gap-2 rounded-md bg-stone-950 px-4 text-sm font-semibold text-white disabled:opacity-50">{busyAction === "password" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}{zh ? "更新密码" : "Update password"}</button>
    </form> : <section className="space-y-2"><h3 className="text-base font-semibold text-stone-950">{zh ? "密码" : "Password"}</h3><p className="text-sm leading-6 text-stone-500">{zh ? "当前账号由第三方登录服务验证身份，没有可在 OpenClass 内修改的独立密码。" : "This account is authenticated by a connected sign-in provider and has no separate OpenClass password."}</p></section>}

    <section className="space-y-4 border-t border-stone-200 pt-7">
      <h3 className="text-base font-semibold text-stone-950">{zh ? "会话与数据" : "Sessions and data"}</h3>
      <p className="text-sm leading-6 text-stone-500">{zh ? "退出所有会话会立即撤销当前账号在所有设备上的登录。数据导出包含账号资料和平台内保存的用户内容。" : "Signing out everywhere revokes this account on every device. The export includes account data and saved user content."}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={() => void revokeSessions()} className="inline-flex h-10 items-center gap-2 rounded-md border border-stone-300 px-4 text-sm font-semibold text-stone-700 disabled:opacity-50"><LogOut className="h-4 w-4" />{zh ? "退出所有会话" : "Sign out everywhere"}</button>
        <button type="button" disabled={busy} onClick={() => void exportData()} className="inline-flex h-10 items-center gap-2 rounded-md border border-stone-300 px-4 text-sm font-semibold text-stone-700 disabled:opacity-50"><Download className="h-4 w-4" />{zh ? "导出我的数据" : "Export my data"}</button>
      </div>
    </section>

    <form className="space-y-4 border-t border-rose-200 pt-7" onSubmit={(event) => void deleteAccount(event)}>
      <div><h3 className="text-base font-semibold text-rose-700">{zh ? "注销账户" : "Delete account"}</h3><p className="mt-2 text-sm leading-6 text-stone-500">{zh ? "注销会删除账号和可删除的用户内容，账务、安全与合规记录可能按法律要求继续保留。此操作无法撤销。" : "Deletion removes your account and deletable content. Billing, security, and compliance records may be retained where required. This cannot be undone."}</p></div>
      {hasPasswordIdentity ? <label className="block text-sm font-semibold text-stone-950">{zh ? "当前密码" : "Current password"}<input required value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} className={inputClass} type="password" autoComplete="current-password" /></label> : null}
      <label className="block text-sm font-semibold text-stone-950">{zh ? "输入 DELETE 确认" : "Type DELETE to confirm"}<input required value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} className={inputClass} autoComplete="off" /></label>
      <button disabled={busy || deleteConfirmation !== "DELETE"} className="inline-flex h-10 items-center gap-2 rounded-md bg-rose-700 px-4 text-sm font-semibold text-white disabled:opacity-50"><Trash2 className="h-4 w-4" />{zh ? "永久注销账户" : "Permanently delete account"}</button>
    </form>

    {message ? <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</p> : null}
    {error ? <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p> : null}
  </div>;
}
