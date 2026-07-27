"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { LoaderCircle, ShieldCheck } from "lucide-react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";

import { api, clearAuthToken } from "@/lib/api";
import type { UserView } from "@/types";

type AuthGateProps = {
  adminOnly?: boolean;
  children: ReactNode;
};

function loginHref() {
  if (typeof window === "undefined") {
    return "/login";
  }
  const next = `${window.location.pathname}${window.location.search}`;
  return `/login?next=${encodeURIComponent(next || "/")}`;
}

export function AuthGate({ adminOnly = false, children }: AuthGateProps) {
  const router = useRouter();
  const { texts: txt } = useInterfaceLanguage();
  const a = txt.auth;
  const [user, setUser] = useState<UserView | null>(null);
  const [isChecking, setIsChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    async function verifySession() {
      try {
        const currentUser = await api.getCurrentUser();
        if (disposed) {
          return;
        }
        if (adminOnly && currentUser.role !== "admin") {
          setError(a.noAdminPermission);
          return;
        }
        setUser(currentUser);
      } catch {
        clearAuthToken();
        if (!disposed) {
          router.replace(loginHref());
        }
      } finally {
        if (!disposed) {
          setIsChecking(false);
        }
      }
    }

    void verifySession();

    return () => {
      disposed = true;
    };
  }, [adminOnly, router, a.noAdminPermission]);

  if (error) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#f7f5ef] px-4 text-stone-950">
        <section className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-6 text-center">
          <ShieldCheck className="mx-auto h-8 w-8 text-stone-400" />
          <h1 className="mt-4 text-xl font-semibold">{error}</h1>
        </section>
      </main>
    );
  }

  if (isChecking || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#f7f5ef] text-stone-500">
        <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
        {a.checking}
      </main>
    );
  }

  return <>{children}</>;
}
