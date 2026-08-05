import type { Metadata } from "next";
import { Suspense } from "react";

import { ResetPasswordPanel } from "@/components/account-recovery-panel";

export const metadata: Metadata = { title: "Reset Password", description: "Use a one-time verification code to set a new OpenClass password." };

export default function ResetPasswordPage() {
  return <Suspense fallback={null}><ResetPasswordPanel /></Suspense>;
}
