import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { PayPalCheckoutPage } from "@/components/paypal-checkout-page";

export const metadata: Metadata = {
  title: "Secure Checkout",
  description: "Top up OpenClass Credits with a PayPal-supported payment method.",
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
