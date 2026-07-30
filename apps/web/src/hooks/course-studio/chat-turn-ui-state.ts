import type {
  BoardFocusRef,
  BoardTaskRequirementSheet,
  CoursePackage,
  TurnIntent,
} from "@/types";

export function activeLessonIdForAsyncPackage(
  coursePackage: CoursePackage,
  requestLessonId: string,
  currentActiveLessonId: string | null,
  fallbackActiveLessonId?: string | null
) {
  if (
    currentActiveLessonId &&
    currentActiveLessonId !== requestLessonId &&
    coursePackage.workspace_tab_order.includes(currentActiveLessonId)
  ) {
    return currentActiveLessonId;
  }
  return fallbackActiveLessonId;
}

export function resolvedBoardFocusForTurn(
  intent: TurnIntent | null | undefined,
  task: BoardTaskRequirementSheet | null | undefined
): BoardFocusRef | null {
  if (intent === "ordinary_chat" || intent === "unclear") {
    return null;
  }
  if (task?.location_status !== "resolved" || !task.target_location) {
    return null;
  }
  return task.target_location;
}
