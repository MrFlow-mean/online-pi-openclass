"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertCircle, ArrowLeft, LoaderCircle, LockKeyhole } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PayPalPaymentMethods } from "@/components/paypal-payment-methods";
import { api } from "@/lib/api";
import type { CreditPackage } from "@/lib/api";

type PayPalCheckoutPageProps = {
  packageId: string | null;
};

export function PayPalCheckoutPage({ packageId }: PayPalCheckoutPageProps) {
  const router = useRouter();
  const [paymentPackage, setPaymentPackage] = useState<CreditPackage | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    async function loadPackage() {
      if (!packageId) {
        setError("请先选择要充值的点数。");
        setLoading(false);
        return;
      }
      try {
        const overview = await api.getCreditWallet();
        const selectedPackage = overview.packages.find((item) => item.id === packageId);
        if (!selectedPackage) throw new Error("该充值套餐不存在或已下架。");
        if (!overview.wallet.paypal_configured) throw new Error("PayPal 收款配置尚未生效。");
        if (!disposed) setPaymentPackage(selectedPackage);
      } catch (loadError) {
        if (!disposed) {
          setError(loadError instanceof Error ? loadError.message : "无法加载支付信息");
        }
      } finally {
        if (!disposed) setLoading(false);
      }
    }

    void loadPackage();
    return () => {
      disposed = true;
    };
  }, [packageId]);

  const handleSuccess = useCallback(async () => {
    router.replace("/wallet?payment=completed");
  }, [router]);

  const handleError = useCallback((message: string) => {
    setNotice(null);
    setError(message);
  }, []);

  const handleNotice = useCallback((message: string) => {
    setError(null);
    setNotice(message);
  }, []);

  return (
    <main className="min-h-screen bg-[#f7f5f0] px-5 py-6 text-stone-950 sm:px-8 sm:py-10">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-center justify-between gap-4">
          <Link
            href="/wallet"
            className="inline-flex items-center gap-2 text-sm font-semibold text-stone-600 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            返回选择点数
          </Link>
          <span className="inline-flex items-center gap-2 text-xs font-medium text-stone-500">
            <LockKeyhole className="h-3.5 w-3.5" /> PayPal 安全支付
          </span>
        </div>

        <section className="mx-auto mt-8 max-w-xl rounded-2xl border border-stone-200 bg-white px-6 py-7 shadow-sm sm:px-10 sm:py-9">
          <div className="text-center">
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-stone-400">
              OpenClass Checkout
            </p>
            <h1 className="mt-2 text-2xl font-semibold">完成支付</h1>
          </div>

          {loading ? (
            <div className="flex items-center justify-center gap-2 py-20 text-sm text-stone-500">
              <LoaderCircle className="h-5 w-5 animate-spin" /> 正在加载支付方式
            </div>
          ) : null}

          {error ? (
            <div className="mt-6 flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}

          {notice ? (
            <div className="mt-6 rounded-xl border border-stone-200 bg-stone-50 p-4 text-sm text-stone-700">
              {notice}
            </div>
          ) : null}

          {paymentPackage ? (
            <PayPalPaymentMethods
              paymentPackage={paymentPackage}
              disabled={busy}
              onBusyChange={setBusy}
              onSuccess={handleSuccess}
              onError={handleError}
              onNotice={handleNotice}
            />
          ) : null}

          {!loading && !paymentPackage ? (
            <Link
              href="/wallet"
              className="mt-6 inline-flex w-full items-center justify-center rounded-lg bg-stone-950 px-4 py-3 text-sm font-semibold text-white"
            >
              返回选择充值金额
            </Link>
          ) : null}
        </section>
      </div>
    </main>
  );
}
