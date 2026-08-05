import type { Metadata } from "next";

import { ContributionDetail } from "@/components/contribution-detail";

export const metadata: Metadata = {
  title: "Course Improvement Proposal",
  description: "View course version differences, discussions, and merge status.",
};

export default async function ContributionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ContributionDetail contributionId={id} />;
}
