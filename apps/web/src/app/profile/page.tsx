import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { ProfileHome } from "@/components/profile-home";

export const metadata: Metadata = {
  title: "个人主页",
  description: "开放课堂的个人项目与收藏项目主页。",
};

type ProfilePageProps = {
  searchParams?: Promise<{
    tab?: string | string[];
    project?: string | string[];
  }>;
};

export default async function ProfilePage({ searchParams }: ProfilePageProps) {
  const params = await searchParams;
  const tabParam = Array.isArray(params?.tab) ? params?.tab[0] : params?.tab;
  const projectParam = Array.isArray(params?.project) ? params?.project[0] : params?.project;

  return (
    <AuthGate>
      <ProfileHome
        initialProjectKey={projectParam}
        initialTab={
          tabParam === "repositories"
            ? "repositories"
            : tabParam === "collaboration"
              ? "collaboration"
              : tabParam === "stars"
                ? "stars"
                : "settings"
        }
      />
    </AuthGate>
  );
}
