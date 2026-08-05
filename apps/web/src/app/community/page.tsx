import type { Metadata } from "next";

import { CommunityHome } from "@/components/community-home";


export const metadata: Metadata = {
  title: "Learning Community · OpenClass",
  description: "Ask questions, discuss, share materials and learning process around any learning topic.",
};


export default function CommunityPage() {
  return <CommunityHome />;
}
