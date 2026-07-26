import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { PayPalCheckoutPage } from "@/components/paypal-checkout-page";

export const metadata: Metadata = {
  title: "安全支付",
  description: "使用 PayPal 支持的付款方式完成 OpenClass 点数充值。",
};

type WalletCheckoutPageProps = {
  searchParams: Promise<{ package?: string | string[] }>;
};

export default async function WalletCheckoutPage({ searchParams }: WalletCheckoutPageProps) {
  const parameters = await searchParams;
  const packageId = typeof parameters.package === "string" ? parameters.package : null;

  return (
    <AuthGate>
      <PayPalCheckoutPage packageId={packageId} />
    </AuthGate>
  );
}
