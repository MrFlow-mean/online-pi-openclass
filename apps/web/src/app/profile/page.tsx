import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { AuthGate } from "@/components/auth-gate";
import { ProfileHome } from "@/components/profile-home";

export const metadata: Metadata = {
  title: "个人主页",
  description: "开放课堂的个人项目与收藏项目主页。",
};

type ProfilePageProps = {
  searchParams?: Promise<{
    tab?: string | string[];
  }>;
};

export default async function ProfilePage({ searchParams }: ProfilePageProps) {
  const params = await searchParams;
  const tabParam = Array.isArray(params?.tab) ? params?.tab[0] : params?.tab;

  if (tabParam === "collaboration") {
    redirect("/profile?tab=repositories");
  }

  return (
    <AuthGate>
      <ProfileHome
        initialTab={
          tabParam === "repositories"
            ? "repositories"
            : tabParam === "stars"
              ? "stars"
              : "settings"
        }
      />
    </AuthGate>
  );
}
