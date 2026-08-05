import { BookOpen } from "lucide-react";

import type { CoursePackage, Lesson } from "@/types";

type CourseGraphPanelProps = {
  activeLesson: Lesson;
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

export function CourseGraphPanel({
  activeLesson,
  relatedEdges,
  lessonMap,
  onOpenLesson,
}: CourseGraphPanelProps) {
  return (
    <div className="border-t border-gray-200 pt-6">
      <div className="mb-4 flex items-center gap-2">
        <BookOpen className="h-4 w-4 text-gray-400" />
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Curriculum Map</p>
      </div>
      <div className="space-y-3">
        {relatedEdges.length ? (
          relatedEdges.map((edge) => {
            const source = lessonMap.get(edge.source_lesson_id);
            const target = lessonMap.get(edge.target_lesson_id);
            if (!source || !target) {
              return null;
            }
            const nextLesson = edge.source_lesson_id === activeLesson.id ? target : source;
            return (
              <button
                key={edge.id}
                type="button"
                onClick={() => void onOpenLesson(nextLesson.id)}
                className="w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:border-gray-300"
              >
                <p className="text-xs font-bold text-gray-900">
                  {source.title} → {target.title}
                </p>
                <p className="mt-1 text-[11px] text-gray-500">relation:{edge.relationship}</p>
              </button>
            );
          })
        ) : (
          <div className="rounded-xl border border-gray-200 bg-white px-4 py-6 text-sm text-gray-500">

            Currently there are no more graph relationships for lesson.
          </div>
        )}
      </div>
    </div>
  );
}
