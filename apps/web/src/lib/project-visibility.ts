import { getApiBase, readEffectiveAuthToken } from "@/lib/api";
import type { BoardDocument, CoursePackage, WorkspaceState } from "@/types";

export type ProjectVisibility = "private" | "public";

export interface PublicLesson {
  id: string;
  title: string;
  summary: string;
  tags: string[];
  board_document: BoardDocument;
  updated_at: string;
}

export interface PublicCoursePackage {
  id: string;
  title: string;
  summary: string;
  lessons: PublicLesson[];
}

export class ProjectVisibilityRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ProjectVisibilityRequestError";
    this.status = status;
  }
}

async function visibilityRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const token = readEffectiveAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    credentials: "include",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new ProjectVisibilityRequestError(
      typeof payload?.detail === "string" ? payload.detail : `Request failed (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export function updateLessonVisibility(lessonId: string, visibility: ProjectVisibility) {
  return visibilityRequest<WorkspaceState>(`/api/lessons/${lessonId}/visibility`, {
    method: "POST",
    body: JSON.stringify({ visibility }),
  });
}

export function updatePackageVisibility(packageId: string, visibility: ProjectVisibility) {
  return visibilityRequest<WorkspaceState>(`/api/packages/${packageId}`, {
    method: "POST",
    body: JSON.stringify({ visibility }),
  });
}

export function getPublicLesson(lessonId: string, historyNodeId?: string) {
  const query = historyNodeId ? `?history_node=${encodeURIComponent(historyNodeId)}` : "";
  return visibilityRequest<PublicLesson>(`/api/public/lessons/${lessonId}${query}`);
}

export function forkPublicLesson(lessonId: string, historyNodeId?: string) {
  const query = historyNodeId ? `?history_node=${encodeURIComponent(historyNodeId)}` : "";
  return visibilityRequest<CoursePackage>(`/api/public/lessons/${lessonId}/fork${query}`, {
    method: "POST",
  });
}

export function getPublicPackage(packageId: string) {
  return visibilityRequest<PublicCoursePackage>(`/api/public/packages/${packageId}`);
}

export function publicProjectHref(kind: "lesson" | "package", id: string) {
  return `/courses/shared/${kind}/${encodeURIComponent(id)}`;
}
