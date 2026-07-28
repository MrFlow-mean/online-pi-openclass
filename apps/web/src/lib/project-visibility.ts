import { getApiBase, readEffectiveAuthToken } from "@/lib/api";
import type { BoardDocument, CoursePackage, WorkspaceState } from "@/types";

export type ProjectVisibility = "private" | "public";

export type PublicationReviewProgressStage =
  | "checking_references"
  | "reading_sources"
  | "reviewing_units"
  | "verifying_sources";

export interface PublicationReviewProgressUpdate {
  stage: PublicationReviewProgressStage;
  completed_items: number;
  total_items: number;
  batch_index: number;
  batch_count: number;
}

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

async function streamingVisibilityRequest(
  path: string,
  visibility: ProjectVisibility,
  onProgress: (progress: PublicationReviewProgressUpdate) => void,
): Promise<WorkspaceState> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = readEffectiveAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${getApiBase()}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify({ visibility }),
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
  if (!response.body) {
    throw new ProjectVisibilityRequestError("Publication review stream was unavailable", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  let workspace: WorkspaceState | null = null;

  const readEvent = (line: string) => {
    const event = JSON.parse(line) as
      | { type: "progress"; progress: PublicationReviewProgressUpdate }
      | { type: "result"; workspace: WorkspaceState }
      | { type: "error"; detail: string };
    if (event.type === "progress") {
      onProgress(event.progress);
    } else if (event.type === "result") {
      workspace = event.workspace;
    } else {
      throw new ProjectVisibilityRequestError(event.detail, response.status);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffered += decoder.decode(value, { stream: !done });
    const lines = buffered.split("\n");
    buffered = lines.pop() ?? "";
    lines.filter(Boolean).forEach(readEvent);
    if (done) {
      break;
    }
  }
  if (buffered.trim()) {
    readEvent(buffered);
  }
  if (!workspace) {
    throw new ProjectVisibilityRequestError("Publication review ended without a result", response.status);
  }
  return workspace;
}

export function updateLessonVisibility(
  lessonId: string,
  visibility: ProjectVisibility,
  onProgress?: (progress: PublicationReviewProgressUpdate) => void,
) {
  if (visibility === "public" && onProgress) {
    return streamingVisibilityRequest(
      `/api/lessons/${lessonId}/visibility/stream`,
      visibility,
      onProgress,
    );
  }
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
