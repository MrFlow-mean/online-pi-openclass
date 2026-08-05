import type { Metadata } from "next";

import { AdminDashboard } from "@/components/admin-dashboard";
import { AuthGate } from "@/components/auth-gate";

export const metadata: Metadata = {
  title: "Admin Dashboard",
  description: "Manage OpenClass users and courses.",
};

export default function AdminPage() {
  return (
    <AuthGate adminOnly>
      <AdminDashboard />
    </AuthGate>
  );
}
