import { getApiBase, readEffectiveAuthToken } from "@/lib/api";
import type {
  CommunityComment,
  CommunityFeedSort,
  CommunityFollowResult,
  CommunityPost,
  CommunityPostDetail,
  CommunitySpace,
  CommunitySpaceSort,
  CommunityVoteResult,
  CreateCommunityPostPayload,
  CreateCommunitySpacePayload,
} from "@/types";


async function communityRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = readEffectiveAuthToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw || `社区请求失败（${response.status}）`;
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      if (typeof parsed.detail === "string") {
        message = parsed.detail;
      }
    } catch {
      // Keep the response body when the server did not return JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}


function queryString(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}


export const communityApi = {
  listSpaces(sort: CommunitySpaceSort = "active") {
    return communityRequest<CommunitySpace[]>(`/api/community/spaces${queryString({ sort })}`);
  },

  createSpace(payload: CreateCommunitySpacePayload) {
    return communityRequest<CommunitySpace>("/api/community/spaces", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  followSpace(slug: string) {
    return communityRequest<CommunityFollowResult>(`/api/community/spaces/${encodeURIComponent(slug)}/follow`, {
      method: "PUT",
    });
  },

  listPosts(options: {
    community?: string;
    tag?: string;
    q?: string;
    sort?: CommunityFeedSort;
    limit?: number;
  } = {}) {
    return communityRequest<CommunityPost[]>(
      `/api/community/posts${queryString({
        community: options.community,
        tag: options.tag,
        q: options.q,
        sort: options.sort ?? "recent",
        limit: options.limit ?? 50,
      })}`
    );
  },

  createPost(payload: CreateCommunityPostPayload) {
    return communityRequest<CommunityPost>("/api/community/posts", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getPost(postId: string) {
    return communityRequest<CommunityPostDetail>(`/api/community/posts/${encodeURIComponent(postId)}`);
  },

  addComment(postId: string, body: string, parentCommentId?: string | null) {
    return communityRequest<CommunityComment>(
      `/api/community/posts/${encodeURIComponent(postId)}/comments`,
      {
        method: "POST",
        body: JSON.stringify({ body, parent_comment_id: parentCommentId ?? null }),
      }
    );
  },

  vote(postId: string, value: -1 | 0 | 1) {
    return communityRequest<CommunityVoteResult>(`/api/community/posts/${encodeURIComponent(postId)}/vote`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },
};
