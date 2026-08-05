import Link from "next/link";
import type { ReactNode } from "react";

import { BrandMark } from "@/components/brand-mark";
import { publicContactEmail } from "@/lib/public-site";

export function LegalPage({ title, summary, children }: { title: string; summary: string; children: ReactNode }) {
  const email = publicContactEmail();
  return (
    <main className="min-h-screen bg-[#f7f5ef] px-4 py-8 text-stone-950 sm:px-6 sm:py-12">
      <article className="mx-auto max-w-3xl rounded-2xl border border-stone-200 bg-white px-6 py-8 shadow-[0_24px_70px_rgba(15,23,42,0.08)] sm:px-10 sm:py-12">
        <header className="border-b border-stone-200 pb-8">
          <Link href="/" className="inline-flex items-center gap-3">
            <BrandMark alt="" className="h-10 w-10 rounded-lg border border-stone-200" size={80} />
            <span className="text-lg font-semibold">OpenClass</span>
          </Link>
          <p className="mt-8 text-xs font-semibold uppercase tracking-[0.2em] text-stone-500">OpenClass Legal</p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-4 text-base leading-7 text-stone-600">{summary}</p>
          <p className="mt-3 text-sm text-stone-500">Last updated: July 28, 2026</p>
        </header>
        <div className="legal-copy mt-8 space-y-8 text-[15px] leading-7 text-stone-700">{children}</div>
        <footer className="mt-10 border-t border-stone-200 pt-6 text-sm leading-6 text-stone-500">

          If you have questions about this page, please contact <a className="font-semibold text-stone-900 underline" href={`mailto:${email}`}>{email}</a>。
          <nav className="mt-4 flex flex-wrap gap-x-5 gap-y-2" aria-label="Law and Security">
            <Link href="/privacy">privacy policy</Link><Link href="/terms">Terms of Service</Link><Link href="/security">Safety instructions</Link>
          </nav>
        </footer>
      </article>
    </main>
  );
}

export function LegalSection({ title, children }: { title: string; children: ReactNode }) {
  return <section><h2 className="text-xl font-semibold text-stone-950">{title}</h2><div className="mt-3 space-y-3">{children}</div></section>;
}
