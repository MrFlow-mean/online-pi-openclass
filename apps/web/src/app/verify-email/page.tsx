import type { Metadata } from "next";

import { EmailVerificationPanel } from "@/components/email-verification-panel";

export const metadata: Metadata = { title: "Verify email", description: "Verify the primary email address of the OpenClass account." };

export default function VerifyEmailPage() {
  return <EmailVerificationPanel />;
}
