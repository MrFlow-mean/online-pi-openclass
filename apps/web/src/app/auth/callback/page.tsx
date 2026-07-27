import type { Metadata } from "next";

import { AuthCallback } from "@/components/auth-callback";

export const metadata: Metadata = {
  title: "正在登录",
  description: "完成开放课堂第三方账号登录。",
};

type AuthCallbackPageProps = {
  searchParams?: Promise<{
    error?: string | string[];
    next?: string | string[];
  }>;
};

function firstParam(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function AuthCallbackPage({ searchParams }: AuthCallbackPageProps) {
  const params = await searchParams;
  return (
    <AuthCallback
      error={firstParam(params?.error)}
      nextPath={firstParam(params?.next)}
    />
  );
}
