import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { ContributionsHub } from "@/components/contributions-hub";

export const metadata: Metadata = {
  title: "Course Collaboration",
  description: "Review received and submitted course improvement proposals.",
};

export default function ContributionsPage() {
  return (
    <AuthGate>
      <ContributionsHub />
    </AuthGate>
  );
}
