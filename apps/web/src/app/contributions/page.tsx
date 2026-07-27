import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { ContributionsHub } from "@/components/contributions-hub";

export const metadata: Metadata = {
  title: "课程协作",
  description: "查看收到和提交的课程改进方案。",
};

export default function ContributionsPage() {
  return (
    <AuthGate>
      <ContributionsHub />
    </AuthGate>
  );
}
