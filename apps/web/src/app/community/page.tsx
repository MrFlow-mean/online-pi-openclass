import type { Metadata } from "next";

import { CommunityHome } from "@/components/community-home";


export const metadata: Metadata = {
  title: "学习社区 · OpenClass",
  description: "围绕任意学习主题提问、讨论、分享资料与学习过程。",
};


export default function CommunityPage() {
  return <CommunityHome />;
}
