import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { LearningHome } from "@/components/learning-home";

export const metadata: Metadata = {
  title: "Learning Home",
  description: "Manage course packages, standalone lessons, activity, and public course discovery.",
};

export default function Home() {
  return (
    <AuthGate>
      <LearningHome />
    </AuthGate>
  );
}
