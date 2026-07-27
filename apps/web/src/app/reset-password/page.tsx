import type { Metadata } from "next";
import { Suspense } from "react";

import { ResetPasswordPanel } from "@/components/account-recovery-panel";

export const metadata: Metadata = { title: "重置密码", description: "使用一次性验证码设置开放课堂账号的新密码。" };

export default function ResetPasswordPage() {
  return <Suspense fallback={null}><ResetPasswordPanel /></Suspense>;
}
