import type { Metadata } from "next";

import { AuthPanel } from "@/components/auth-panel";

export const metadata: Metadata = {
  title: "Create Account",
  description: "Create an OpenClass account.",
};

export default function RegisterPage() {
  return <AuthPanel initialMode="register" />;
}
