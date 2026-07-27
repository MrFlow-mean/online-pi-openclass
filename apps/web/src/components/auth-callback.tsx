"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";

import { api } from "@/lib/api";
import { loginRedirectPath } from "@/lib/auth-redirect";

type AuthCallbackProps = {
  error?: string | null;
  nextPath?: string | null;
};

export function AuthCallback({ error, nextPath }: AuthCallbackProps) {
  const router = useRouter();
  const hasError = Boolean(error);
  const message = error || "正在安全地确认登录会话。";

  useEffect(() => {
    if (error) {
      return;
    }
    let disposed = false;
    api.getCurrentUser()
      .then(() => {
        if (!disposed) {
          router.replace(loginRedirectPath(nextPath));
        }
      })
      .catch(() => {
        if (!disposed) {
          router.replace(`/login?error=${encodeURIComponent("第三方登录没有建立有效会话")}`);
        }
      });
    return () => {
      disposed = true;
    };
  }, [error, nextPath, router]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f7f5ef] px-4 text-stone-950">
      <section className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-6 text-center shadow-[0_24px_70px_rgba(15,23,42,0.12)]">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-stone-950 text-white">
          {hasError ? <TriangleAlert className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">{hasError ? "登录未完成" : "登录成功"}</h1>
        <p className="mt-3 text-sm leading-6 text-stone-600">
          {message}
        </p>
        {!hasError ? <LoaderCircle className="mx-auto mt-5 h-5 w-5 animate-spin text-stone-400" /> : null}
        {hasError ? (
          <Link
            href="/login"
            className="mt-6 inline-flex h-10 items-center justify-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
          >
            返回登录
          </Link>
        ) : null}
      </section>
    </main>
  );
}
