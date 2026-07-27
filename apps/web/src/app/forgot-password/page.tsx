import type { Metadata } from "next";

import { ForgotPasswordPanel } from "@/components/account-recovery-panel";

export const metadata: Metadata = { title: "找回密码", description: "通过注册邮箱重置开放课堂账号密码。" };

export default function ForgotPasswordPage() {
  return <ForgotPasswordPanel />;
}
