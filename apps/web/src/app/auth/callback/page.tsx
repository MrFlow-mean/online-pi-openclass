import type { Metadata } from "next";

import { AuthCallback } from "@/components/auth-callback";

export const metadata: Metadata = {
  title: "Signing In",
  description: "Complete sign-in with a connected account.",
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
