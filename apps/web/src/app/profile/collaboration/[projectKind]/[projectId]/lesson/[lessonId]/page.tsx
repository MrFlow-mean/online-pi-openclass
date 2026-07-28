import { notFound } from "next/navigation";

import { AuthGate } from "@/components/auth-gate";
import { CollaborativeLessonEditor } from "@/components/collaborative-lesson-editor";

export default async function CollaborativeLessonPage({
  params,
}: {
  params: Promise<{ projectKind: string; projectId: string; lessonId: string }>;
}) {
  const { projectKind, projectId, lessonId } = await params;
  if (projectKind !== "lesson" && projectKind !== "package") notFound();
  return (
    <AuthGate>
      <CollaborativeLessonEditor projectKind={projectKind} projectId={projectId} lessonId={lessonId} />
    </AuthGate>
  );
}
