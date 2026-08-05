import Link from "next/link";
import type { ReactNode } from "react";

import { BrandMark } from "@/components/brand-mark";

export function AuthFormShell({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f7f5ef] px-4 py-10 text-stone-950">
      <section className="w-full max-w-md rounded-2xl border border-stone-200 bg-white p-6 shadow-[0_24px_70px_rgba(15,23,42,0.1)] sm:p-8">
        <Link href="/" className="inline-flex items-center gap-3 text-stone-950">
          <BrandMark alt="" className="h-10 w-10 rounded-lg border border-stone-200" size={80} />
          <span className="text-lg font-semibold">OpenClass</span>
        </Link>
        <h1 className="mt-7 text-3xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-3 text-sm leading-6 text-stone-600">{description}</p>
        <div className="mt-7">{children}</div>
        <p className="mt-7 text-center text-sm text-stone-500">
          <Link href="/login" className="font-semibold text-stone-950 underline underline-offset-4">

            Return to login
          </Link>
        </p>
      </section>
    </main>
  );
}
