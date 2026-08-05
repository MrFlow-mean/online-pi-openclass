import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { WalletHome } from "@/components/wallet-home";

export const metadata: Metadata = {
  title: "Credits and top-up",
  description: "View OpenClass Credits and choose a top-up amount.",
};

export default function WalletPage() {
  return (
    <AuthGate>
      <WalletHome />
    </AuthGate>
  );
}
