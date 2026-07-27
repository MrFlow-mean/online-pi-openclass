"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Coins,
  LoaderCircle,
  ShieldCheck,
  WalletCards,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { CreditTransaction, CreditWalletOverview } from "@/lib/api";

function transactionLabel(transaction: CreditTransaction) {
  if (transaction.kind === "paypal_top_up") {
    return "PayPal 充值";
  }
  if (transaction.kind === "paypal_refund") {
    return "PayPal 退款";
  }
  if (transaction.kind === "model_usage") {
    return "模型调用";
  }
  return "积分变动";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function WalletHome() {
  const router = useRouter();
  const [overview, setOverview] = useState<CreditWalletOverview | null>(null);
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [navigatingPackageId, setNavigatingPackageId] = useState<string | null>(null);
  const [processingReturn, setProcessingReturn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const returnHandled = useRef(false);

  const loadWallet = useCallback(async () => {
    const [nextOverview, nextTransactions] = await Promise.all([
      api.getCreditWallet(),
      api.getCreditTransactions(),
    ]);
    setOverview(nextOverview);
    setTransactions(nextTransactions);
  }, []);

  useEffect(() => {
    let disposed = false;

    async function initialize() {
      try {
        const params = new URLSearchParams(window.location.search);
        const paypalState = params.get("paypal");
        const paymentState = params.get("payment");
        const orderId = params.get("token");
        if (paypalState === "approved" && orderId && !returnHandled.current) {
          returnHandled.current = true;
          setProcessingReturn(true);
          await api.capturePayPalOrder(orderId);
          if (!disposed) {
            setNotice("付款已完成，OpenClass 已确认收到这笔款项。积分已到账。");
            window.history.replaceState({}, "", "/wallet");
          }
        } else if (paypalState === "cancelled") {
          setNotice("你已取消本次付款，没有产生扣款。");
          window.history.replaceState({}, "", "/wallet");
        } else if (paymentState === "completed") {
          setNotice("付款已完成，OpenClass 已确认收到这笔款项。积分已到账。");
          window.history.replaceState({}, "", "/wallet");
        }
        await loadWallet();
      } catch (loadError) {
        if (!disposed) {
          setError(loadError instanceof Error ? loadError.message : "无法加载支付账户");
        }
      } finally {
        if (!disposed) {
          setLoading(false);
          setProcessingReturn(false);
        }
      }
    }

    void initialize();
    return () => {
      disposed = true;
    };
  }, [loadWallet]);

  const wallet = overview?.wallet;

  return (
    <main className="min-h-screen bg-[#f7f5f0] px-5 py-8 text-stone-950 sm:px-8">
      <div className="mx-auto max-w-4xl">
        <Link
          href="/home"
          className="inline-flex items-center gap-2 text-sm font-semibold text-stone-600 hover:text-stone-950"
        >
          <ArrowLeft className="h-4 w-4" />
          返回主页
        </Link>

        <div className="mt-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.25em] text-stone-400">
              OpenClass Credits
            </p>
            <h1 className="mt-2 text-3xl font-semibold">积分与充值</h1>
            <p className="mt-2 text-sm text-stone-500">
              选择金额后，使用 PayPal 支持的付款方式安全充值。
            </p>
          </div>
          <Coins className="h-10 w-10 text-amber-500" />
        </div>

        {processingReturn ? (
          <section className="mt-8 flex items-center gap-3 rounded-2xl border border-blue-200 bg-blue-50 p-5 text-sm font-medium text-blue-950">
            <LoaderCircle className="h-5 w-5 animate-spin" />
            正在向 PayPal 确认付款结果，请不要关闭页面。
          </section>
        ) : null}

        {error ? (
          <section className="mt-8 flex items-start gap-3 rounded-2xl border border-red-200 bg-red-50 p-5 text-sm text-red-900">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
            <span>{error}</span>
          </section>
        ) : null}

        {notice ? (
          <section className="mt-8 flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-5 text-sm text-emerald-950">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" />
            <span>{notice}</span>
          </section>
        ) : null}

        <section className="mt-8 rounded-2xl border border-stone-200 bg-white p-6 shadow-sm">
          <div className="flex items-start gap-3">
            <WalletCards className="mt-0.5 h-5 w-5 shrink-0 text-blue-700" />
            <div className="w-full">
              <h2 className="font-semibold">选择充值金额</h2>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                付款由 PayPal 处理。OpenClass 不接触或保存你的银行卡信息。
              </p>
              <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {(overview?.packages ?? []).map((paymentPackage) => {
                  const isNavigating = navigatingPackageId === paymentPackage.id;
                  return (
                    <button
                      key={paymentPackage.id}
                      type="button"
                      onClick={() => {
                        setError(null);
                        setNotice(null);
                        setNavigatingPackageId(paymentPackage.id);
                        router.push(`/wallet/checkout?package=${encodeURIComponent(paymentPackage.id)}`);
                      }}
                      disabled={loading || !wallet?.paypal_configured || navigatingPackageId !== null}
                      className="rounded-xl border border-stone-200 bg-stone-50 px-4 py-4 text-left transition hover:border-blue-400 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="block text-xl font-semibold">${paymentPackage.amount_usd}</span>
                      <span className="mt-1 block text-sm text-stone-600">
                        获得 {paymentPackage.credits.toLocaleString()} 点数
                      </span>
                      <span className="mt-3 inline-flex items-center gap-2 text-sm font-semibold text-blue-700">
                        {isNavigating ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
                        {isNavigating ? "正在打开支付页面" : "前往支付"}
                      </span>
                    </button>
                  );
                })}
              </div>
              {!loading && !wallet?.paypal_configured ? (
                <p className="mt-4 text-sm text-red-700">PayPal 收款配置尚未在当前服务中生效。</p>
              ) : null}
            </div>
          </div>
        </section>

        <section className="mt-6 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm leading-6 text-amber-950">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" />
          <p>Credits 是 OpenClass 平台使用额度，不是现金，不可转让或提现。</p>
        </section>

        {wallet && wallet.balance_credits > 0 && wallet.model_access_status !== "ready" ? (
          <section
            className={`mt-6 flex items-start gap-3 rounded-2xl border p-5 text-sm leading-6 ${
              wallet.model_access_status === "syncing"
                ? "border-blue-200 bg-blue-50 text-blue-950"
                : "border-red-200 bg-red-50 text-red-900"
            }`}
          >
            {wallet.model_access_status === "syncing" ? (
              <LoaderCircle className="mt-0.5 h-5 w-5 shrink-0 animate-spin" />
            ) : (
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
            )}
            <p>
              {wallet.model_access_status === "syncing"
                ? "模型额度同步中，点数已经到账。"
                : "模型额度暂不可用，请稍后重试或联系支持。"}
            </p>
          </section>
        ) : null}

        <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-6 shadow-sm">
          <h2 className="font-semibold">交易明细</h2>
          {loading ? (
            <p className="mt-4 flex items-center gap-2 text-sm text-stone-500">
              <LoaderCircle className="h-4 w-4 animate-spin" /> 正在加载交易记录
            </p>
          ) : transactions.length === 0 ? (
            <p className="mt-4 rounded-xl border border-dashed border-stone-200 px-4 py-8 text-center text-sm text-stone-500">
              还没有交易记录。
            </p>
          ) : (
            <div className="mt-4 divide-y divide-stone-100">
              {transactions.map((transaction) => (
                <div key={transaction.entry_id} className="flex items-center justify-between gap-4 py-4">
                  <div>
                    <p className="text-sm font-semibold">{transactionLabel(transaction)}</p>
                    <p className="mt-1 text-xs text-stone-500">{formatDate(transaction.created_at)}</p>
                  </div>
                  <div className="text-right">
                    <p className={transaction.delta_credits >= 0 ? "font-semibold text-emerald-700" : "font-semibold text-stone-700"}>
                      {transaction.delta_credits >= 0 ? "+" : ""}
                      {transaction.delta_credits.toLocaleString()} Credits
                    </p>
                    <p className="mt-1 text-xs text-stone-500">
                      余额 {transaction.balance_after.toLocaleString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
