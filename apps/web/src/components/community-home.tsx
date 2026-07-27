"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, LoaderCircle, RefreshCw, UsersRound } from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import { api } from "@/lib/api";
import { communityApi } from "@/lib/community-api";
import type { CommunityIntegration, UserView } from "@/types";


function publicCommunityDestination(publicUrl: string) {
  try {
    const current = new URL(window.location.href);
    const destination = new URL(publicUrl, current);
    const normalizedCurrentPath = current.pathname.replace(/\/+$/, "") || "/";
    const normalizedDestinationPath = destination.pathname.replace(/\/+$/, "") || "/";
    if (
      destination.origin === current.origin &&
      normalizedDestinationPath === normalizedCurrentPath &&
      !destination.pathname.endsWith("/")
    ) {
      destination.pathname = `${destination.pathname}/`;
      return destination.toString();
    }
  } catch {
    // Let the browser handle adapter-provided URLs that are not URL-parseable here.
  }
  return publicUrl;
}

function communityShareDestination(
  integration: CommunityIntegration,
  useSingleSignOn: boolean
) {
  try {
    const current = new URL(window.location.href);
    const encodedPrefill = communitySharePrefill(current);
    if (!encodedPrefill || !integration.public_url) {
      return null;
    }
    const communityRoot = new URL(integration.public_url, current);
    const communityPath = communityRoot.pathname.replace(/\/+$/, "");
    const destination = new URL(communityRoot);
    destination.pathname = `${communityPath}/questions/add`;
    destination.search = "";
    destination.searchParams.set("prefill", encodedPrefill);

    if (useSingleSignOn && destination.origin === current.origin) {
      const answerPath = `${destination.pathname.slice(communityPath.length)}${destination.search}`;
      window.localStorage.setItem("_a_rp_", answerPath);
      return integration.entry_url;
    }
    return destination.toString();
  } catch {
    return null;
  }
}

function answerPrefillValue(value: string) {
  // Answer decodes the query once through URLSearchParams and once in its prefill parser.
  return encodeURIComponent(value.replaceAll("%", "%25"));
}

function communitySharePrefill(current: URL) {
  const existingPrefill = current.searchParams.get("prefill");
  if (existingPrefill) {
    return existingPrefill;
  }
  if (current.searchParams.get("reference") !== "history_node") {
    return null;
  }
  const lessonId = current.searchParams.get("lesson_id")?.trim();
  const historyNodeId = current.searchParams.get("history_node")?.trim();
  if (!lessonId || !historyNodeId) {
    return null;
  }
  const referenceUrl = new URL(
    `/courses/shared/lesson/${encodeURIComponent(lessonId)}`,
    current.origin
  );
  referenceUrl.searchParams.set("history_node", historyNodeId);
  return answerPrefillValue(
    `> [课堂历史节点引用 · 点击打开](${referenceUrl.toString()})`
  );
}


function CommunityStatusPage({
  integration,
  error,
  loading,
}: {
  integration: CommunityIntegration | null;
  error: string;
  loading: boolean;
}) {
  const available = integration?.available ?? false;
  const ssoReady = integration?.sso_enabled && !integration.setup_required;
  const title = loading
    ? "正在连接学习社区"
    : available && !ssoReady
      ? "社区单点登录尚未就绪"
      : "学习社区暂时不可用";
  const detail = loading
    ? "正在确认社区服务与登录状态。"
    : error || (available
      ? "Apache Answer 已运行，但 OpenClass 单点登录连接器尚未完成配置。"
      : "OpenClass 无法连接 Apache Answer，请检查社区服务状态。");

  return (
    <main className="min-h-screen bg-[#f5f3ee] text-stone-900">
      <header className="border-b border-stone-200/80 bg-[#f5f3ee]/92">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link href="/home" aria-label="返回学习主页" className="flex items-center gap-2">
            <BrandMark className="h-8 w-8" />
            <span className="text-sm font-bold tracking-tight">OpenClass</span>
          </Link>
          <AccountMenu compact />
        </div>
      </header>

      <section className="mx-auto flex max-w-3xl flex-col items-center px-5 py-20 text-center sm:py-28">
        <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-sky-100 text-sky-700">
          {loading ? <LoaderCircle className="h-8 w-8 animate-spin" /> : available ? <UsersRound className="h-8 w-8" /> : <AlertTriangle className="h-8 w-8" />}
        </span>
        <p className="mt-6 text-xs font-semibold uppercase tracking-[0.2em] text-sky-700">OpenClass Community</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-stone-950 sm:text-4xl">{title}</h1>
        <p className="mt-4 max-w-xl whitespace-pre-line text-sm leading-7 text-stone-600">{detail}</p>

        {!loading ? (
          <div className="mt-7 flex flex-wrap justify-center gap-3">
            <button type="button" onClick={() => window.location.reload()} className="inline-flex items-center gap-2 rounded-xl bg-stone-950 px-5 py-3 text-sm font-semibold text-white">
              <RefreshCw className="h-4 w-4" />重新检查
            </button>
            {integration?.public_url ? (
              <a href={integration.public_url} className="inline-flex items-center gap-2 rounded-xl border border-stone-300 bg-white px-5 py-3 text-sm font-semibold text-stone-700">
                <ExternalLink className="h-4 w-4" />查看社区服务
              </a>
            ) : null}
          </div>
        ) : null}
      </section>
    </main>
  );
}


export function CommunityHome() {
  const [integration, setIntegration] = useState<CommunityIntegration | null>(null);
  const [user, setUser] = useState<UserView | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([
      communityApi.getIntegration(),
      api.getCurrentUser().catch(() => null),
    ]).then(([nextIntegration, nextUser]) => {
      if (!active) return;
      setIntegration(nextIntegration);
      setUser(nextUser);
      setAuthChecked(true);
    }).catch((requestError) => {
      if (!active) return;
      setError(requestError instanceof Error ? requestError.message : "社区服务状态检查失败");
      setAuthChecked(true);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!integration || !authChecked) return;
    const registeredUser = user?.role === "user" || user?.role === "admin";
    const ssoReady = integration.available && integration.sso_enabled && !integration.setup_required;
    const shareDestination = integration.available
      ? communityShareDestination(integration, Boolean(registeredUser && ssoReady))
      : null;
    const destination = shareDestination ?? (
      registeredUser && ssoReady
        ? integration.entry_url
        : integration.available
          ? integration.public_url && publicCommunityDestination(integration.public_url)
          : null
    );
    if (destination) {
      window.location.replace(destination);
    }
  }, [authChecked, integration, user]);

  const registeredUser = user?.role === "user" || user?.role === "admin";
  const ssoReady = integration?.available && integration.sso_enabled && !integration.setup_required;
  const hasDestination = Boolean(integration && (
    registeredUser && ssoReady ? integration.entry_url : integration.available && integration.public_url
  ));
  const loading = (!authChecked && !error) || hasDestination;

  return <CommunityStatusPage integration={integration} error={error} loading={loading} />;
}
