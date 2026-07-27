import { getApiBase, readEffectiveAuthToken } from "@/lib/api";
import type { GeometryGenerationPayload, GeometryScene } from "@/types/geometry";

async function geometryErrorMessage(response: Response) {
  const text = await response.text();
  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    // Preserve the response text when the server did not return JSON.
  }
  return text || `图形生成请求失败（${response.status}）`;
}

export async function generateGeometryScene(
  lessonId: string,
  payload: GeometryGenerationPayload,
  options: { signal?: AbortSignal } = {}
): Promise<GeometryScene> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = readEffectiveAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(
    `${getApiBase()}/api/lessons/${encodeURIComponent(lessonId)}/geometry/generate`,
    {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      cache: "no-store",
      credentials: "include",
      signal: options.signal,
    }
  );
  if (!response.ok) {
    throw new Error(await geometryErrorMessage(response));
  }
  return response.json() as Promise<GeometryScene>;
}
