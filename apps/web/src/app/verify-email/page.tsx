import type { Metadata } from "next";

import { EmailVerificationPanel } from "@/components/email-verification-panel";

export const metadata: Metadata = { title: "验证邮箱", description: "验证开放课堂账号的主邮箱。" };

export default function VerifyEmailPage() {
  return <EmailVerificationPanel />;
}
