import type { Metadata } from "next";

import { TrendingCourses } from "@/components/trending-courses";

export const metadata: Metadata = {
  title: "Trending Courses",
  description: "Explore popular public course projects on OpenClass.",
};

export default function TrendingPage() {
  return <TrendingCourses />;
}
