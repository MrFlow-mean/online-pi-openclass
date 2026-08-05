import type { Metadata } from "next";

import { ForgotPasswordPanel } from "@/components/account-recovery-panel";

export const metadata: Metadata = { title: "Forgot Password", description: "Reset your OpenClass password through your registered email address." };

export default function ForgotPasswordPage() {
  return <ForgotPasswordPanel />;
}
