import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { FollowingFeed } from "@/components/following-feed";

export const metadata: Metadata = {
  title: "Following Activity",
  description: "View updates from courses and creators you follow.",
};

export default function FollowingPage() {
  return (
    <AuthGate>
      <FollowingFeed />
    </AuthGate>
  );
}
