import type { Metadata } from "next";

import { ContributionDetail } from "@/components/contribution-detail";

export const metadata: Metadata = {
  title: "课程改进方案",
  description: "查看课程版本差异、讨论和合并状态。",
};

export default async function ContributionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ContributionDetail contributionId={id} />;
}
