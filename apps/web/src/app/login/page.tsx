import type { Metadata } from "next";

import { AuthPanel } from "@/components/auth-panel";

export const metadata: Metadata = {
  title: "Log in",
  description: "Log in to your OpenClass account.",
};

export default function LoginPage() {
  return <AuthPanel initialMode="login" />;
}
