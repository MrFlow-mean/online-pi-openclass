import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { CourseStudio } from "@/components/course-studio";

export const metadata: Metadata = {
  title: "Studio",
  description: "Edit lessons, reference sources, and learn with AI in OpenClass Studio.",
};

export default function StudioPage() {
  return (
    <AuthGate allowGuest>
      <CourseStudio />
    </AuthGate>
  );
}
