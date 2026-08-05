import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Boxes,
  Code2,
  Database,
  GitBranchPlus,
  Route,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";

import { BrandMark } from "@/components/brand-mark";

const GITHUB_REPOSITORY_URL = "https://github.com/MrFlow-mean/openclass";

const architectureSections = [
  {
    title: "Web front-end",
    icon: Code2,
    body: "Next.js App Router hosts the learning homepage, course workbench, personal homepage and popular courses; the component is responsible for interface combination, and the API client and pure tools are placed in src/lib.",
  },
  {
    title: "API backend",
    icon: TerminalSquare,
    body: "FastAPI exposes workspace, documents, chat and realtime boundaries; the router handles HTTP, and business status and transactions remain in services.",
  },
  {
    title: "Data and documents",
    icon: Database,
    body: "SQLite saves courses, history and account status; export files and AI call logs are written to the persistence directory and do not enter the code warehouse.",
  },
  {
    title: "AI collaboration boundaries",
    icon: ShieldCheck,
    body: "Chatbot, BoardEditor, Resolver and Requirement Manager collaborate according to a fixed protocol to avoid writing on the board, locating and explaining authorization into a free answer.",
  },
] as const;

const workflowSteps = [
  ["TurnDecision", "Determine the request type of this round and the current writing status."],
  ["ResolveTarget", "Locate boards, selections, evidence, or conversational context."],
  ["BuildContext", "Only construct the minimum context required for this round of actions."],
  ["ExecuteRole", "A single main character performs writing, narration, clarification or interaction."],
  ["PersistHistory", "Write lesson commit, task version, events and metadata."],
] as const;

const verificationCommands = [
  ["npm run lint:web", "Front-end lint"],
  ["npm run typecheck:web", "TypeScript type checking"],
  ["npm run test:api", "backendpytest"],
  ["npm run build:web", "Next.js production build"],
  ["npm run verify", "Complete access control before submission"],
] as const;

export const metadata: Metadata = {
  title: "Project documentation",
  description: "OpenClass's project structure, AI collaboration boundaries, local run and verification command instructions.",
};

export default function TechDocsPage() {
  return (
    <main className="min-h-screen bg-[#f8fafc] text-slate-950">
      <section className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-col gap-7 px-5 py-8 sm:px-8 lg:flex-row lg:items-end lg:justify-between lg:py-12">
          <div className="max-w-3xl">
            <Link
              href="/home"
              className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 transition hover:text-slate-950"
            >
              <ArrowLeft className="h-4 w-4" />

              Return to study home page
            </Link>
            <div className="mt-8 flex items-center gap-4">
              <BrandMark className="h-14 w-14 rounded-lg border border-slate-200 bg-white shadow-sm" priority size={112} />
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">OpenClass Docs</p>
                <h1 className="mt-2 text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl">Project documentation</h1>
              </div>
            </div>
            <p className="mt-6 max-w-2xl text-base leading-8 text-slate-600">

              This page summarizes the current OpenClass repository structure, runtime boundaries, AI collaboration protocol, and verification commands. It documents the platform architecture without embedding subject templates, fixed lessons, or demo content.
            </p>
          </div>

          <a
            href={GITHUB_REPOSITORY_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-11 w-fit items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800"
          >

            Open GitHub
            <ArrowUpRight className="h-4 w-4" />
          </a>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-6 px-5 py-8 sm:px-8 lg:grid-cols-[16rem_minmax(0,1fr)] lg:py-10">
        <aside className="h-fit border-b border-slate-200 pb-5 lg:sticky lg:top-6 lg:border-b-0 lg:pb-0">
          <nav aria-label="Project document directory" className="grid gap-2 text-sm font-medium text-slate-600">
            <a href="#architecture" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">

              Architecture overview
            </a>
            <a href="#workflow" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">

              AI workflow
            </a>
            <a href="#verification" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">

              Verify command
            </a>
          </nav>
        </aside>

        <div className="min-w-0 space-y-10">
          <section id="architecture" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <Boxes className="h-5 w-5 text-slate-500" />
              <h2 className="text-2xl font-semibold tracking-tight text-slate-950">Architecture overview</h2>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {architectureSections.map((section) => {
                const Icon = section.icon;
                return (
                  <article key={section.title} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="flex items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-950 text-white">
                        <Icon className="h-5 w-5" />
                      </span>
                      <h3 className="text-base font-semibold text-slate-950">{section.title}</h3>
                    </div>
                    <p className="mt-4 text-sm leading-7 text-slate-600">{section.body}</p>
                  </article>
                );
              })}
            </div>
          </section>

          <section id="workflow" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <Route className="h-5 w-5 text-slate-500" />
              <h2 className="text-2xl font-semibold tracking-tight text-slate-950">AI workflow</h2>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <ol className="grid gap-4">
                {workflowSteps.map(([name, description], index) => (
                  <li key={name} className="grid gap-3 sm:grid-cols-[9rem_minmax(0,1fr)]">
                    <div className="flex items-center gap-2">
                      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-xs font-semibold text-slate-700">
                        {index + 1}
                      </span>
                      <span className="font-mono text-sm font-semibold text-slate-950">{name}</span>
                    </div>
                    <p className="text-sm leading-7 text-slate-600">{description}</p>
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <section id="verification" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <TerminalSquare className="h-5 w-5 text-slate-500" />
              <h2 className="text-2xl font-semibold tracking-tight text-slate-950">Verify command</h2>
            </div>
            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
              {verificationCommands.map(([command, description]) => (
                <div key={command} className="grid gap-2 border-b border-slate-200 px-5 py-4 last:border-b-0 sm:grid-cols-[13rem_minmax(0,1fr)]">
                  <code className="rounded-md bg-slate-100 px-2 py-1 font-mono text-sm font-semibold text-slate-900">
                    {command}
                  </code>
                  <p className="text-sm text-slate-600">{description}</p>
                </div>
              ))}
            </div>

            <div className="mt-5 flex flex-wrap gap-3">
              <Link
                href="/home"
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-950"
              >
                <BookOpen className="h-4 w-4" />

                Return to study homepage
              </Link>
              <a
                href={GITHUB_REPOSITORY_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                <GitBranchPlus className="h-4 w-4" />

                Open the source code repository
              </a>
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}
