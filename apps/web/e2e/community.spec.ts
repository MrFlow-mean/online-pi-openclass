import { expect, test } from "@playwright/test";


test("browses a community post, votes, follows, and joins the discussion", async ({ page }) => {
  const now = "2026-07-24T10:00:00+00:00";
  const space = {
    id: "community-learning",
    slug: "共同学习",
    name: "共同学习",
    description: "交流问题、方法和学习成果",
    creator_user_id: "user-author",
    created_at: now,
    updated_at: now,
    post_count: 1,
    follower_count: 2,
  };
  const post = {
    id: "post-evidence",
    community_id: space.id,
    community_slug: space.slug,
    community_name: space.name,
    author_user_id: "user-author",
    author_display_name: "学习者甲",
    post_type: "question",
    title: "如何验证自己的理解是否可靠？",
    body: "我希望比较几种可以重复使用的理解验证方法。",
    tags: ["理解检查", "学习方法"],
    vote_score: 3,
    comment_count: 0,
    viewer_vote: 0,
    created_at: now,
    updated_at: now,
  };
  const comments: Array<Record<string, unknown>> = [];

  await page.context().addCookies([
    { name: "openclass.auth.token", value: "community-test-token", domain: "127.0.0.1", path: "/" },
  ]);

  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      id: "user-reader",
      email: "reader@example.com",
      role: "user",
      display_name: "测试学习者",
      avatar_url: null,
      created_at: now,
      last_login_at: now,
      auth_identities: [],
    }),
  }));

  await page.route("**/api/community/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/community/spaces" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([space]) });
      return;
    }
    if (path === "/api/community/posts" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([post]) });
      return;
    }
    if (path === `/api/community/posts/${post.id}` && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ post, comments }) });
      return;
    }
    if (path === `/api/community/posts/${post.id}/vote` && method === "PUT") {
      post.viewer_vote = 1;
      post.vote_score = 4;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ post_id: post.id, viewer_vote: 1, vote_score: 4 }) });
      return;
    }
    if (path === `/api/community/posts/${post.id}/comments` && method === "POST") {
      const payload = request.postDataJSON() as { body: string; parent_comment_id?: string | null };
      const comment = {
        id: "comment-new",
        post_id: post.id,
        parent_comment_id: payload.parent_comment_id ?? null,
        author_user_id: "user-reader",
        author_display_name: "测试学习者",
        body: payload.body,
        created_at: now,
        updated_at: now,
      };
      comments.push(comment);
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(comment) });
      return;
    }
    if (path === `/api/community/spaces/${encodeURIComponent(space.slug)}/follow` && method === "PUT") {
      space.follower_count = 3;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ community_id: space.id, following: true, follower_count: 3 }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Unexpected test request" }) });
  });

  await page.goto("/community");
  await expect(page.getByRole("button", { name: "最新", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /如何验证自己的理解是否可靠/ }).click();
  await expect(page.getByRole("heading", { name: post.title })).toBeVisible();

  await page.getByRole("button", { name: "赞同帖子", exact: true }).click();
  await expect(page.getByText("4", { exact: true })).toBeVisible();

  await page.getByLabel("写评论").fill("我会先尝试复述，再用反例检查边界。 ");
  await page.getByRole("button", { name: "参与讨论" }).click();
  await expect(page.getByText("我会先尝试复述，再用反例检查边界。")).toBeVisible();

  await page.getByRole("button", { name: "返回帖子列表" }).click();
  await page.getByRole("button", { name: /共同学习/ }).first().click();
  await page.getByRole("button", { name: "关注社区" }).click();
  await expect(page.getByRole("button", { name: "已关注" })).toBeDisabled();
});


test("creates a topic-neutral community and post through the composer", async ({ page }) => {
  const now = "2026-07-24T10:00:00+00:00";
  const spaces: Array<Record<string, unknown>> = [];
  const posts: Array<Record<string, unknown>> = [];

  await page.context().addCookies([
    { name: "openclass.auth.token", value: "community-test-token", domain: "127.0.0.1", path: "/" },
  ]);

  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ id: "user-author", email: "author@example.com", role: "user", display_name: "发布者", avatar_url: null, created_at: now, last_login_at: now, auth_identities: [] }),
  }));

  await page.route("**/api/community/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path === "/api/community/spaces" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(spaces) });
      return;
    }
    if (path === "/api/community/spaces" && method === "POST") {
      const payload = request.postDataJSON() as { name: string; description: string };
      const space = { id: "community-new", slug: "知识可视化", name: payload.name, description: payload.description, creator_user_id: "user-author", created_at: now, updated_at: now, post_count: 0, follower_count: 0 };
      spaces.push(space);
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(space) });
      return;
    }
    if (path === "/api/community/posts" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(posts) });
      return;
    }
    if (path === "/api/community/posts" && method === "POST") {
      const payload = request.postDataJSON() as { community_slug: string; post_type: string; title: string; body: string; tags: string[] };
      const post = { id: "post-new", community_id: "community-new", community_slug: payload.community_slug, community_name: "知识可视化", author_user_id: "user-author", author_display_name: "发布者", post_type: payload.post_type, title: payload.title, body: payload.body, tags: payload.tags, vote_score: 0, comment_count: 0, viewer_vote: 0, created_at: now, updated_at: now };
      posts.push(post);
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(post) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Unexpected test request" }) });
  });

  await page.goto("/community");
  await page.getByRole("button", { name: "创建社区" }).click();
  await page.getByLabel("社区名称").fill("知识可视化");
  await page.getByLabel("社区说明").fill("交流如何把抽象知识转化为可探索的表达");
  await page.getByRole("button", { name: "创建社区", exact: true }).last().click();
  await expect(page.getByText("知识可视化").first()).toBeVisible();

  await page.getByRole("button", { name: "发布内容" }).click();
  await page.getByLabel("内容类型").selectOption("study_note");
  await page.getByLabel("标题").fill("一次知识结构图的迭代记录");
  await page.getByLabel("正文").fill("我把概念之间的关系从线性列表改成了可以展开的关系图，并记录了验证过程。");
  await page.getByLabel("标签").fill("知识结构, 可视化");
  await page.getByRole("button", { name: "发布帖子" }).click();

  await expect(page.getByRole("button", { name: /一次知识结构图的迭代记录/ })).toBeVisible();
});


test("keeps community reading public and sends anonymous writers to login", async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: "未登录" }),
  }));
  await page.route("**/api/community/spaces**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: "[]",
  }));
  await page.route("**/api/community/posts**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: "[]",
  }));

  await page.goto("/community");

  await expect(page).toHaveURL(/\/community$/);
  await expect(page.getByRole("button", { name: "最新", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect(page).toHaveURL(/\/login\?next=%2Fcommunity$/);
});
