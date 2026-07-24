import { expect, test } from "@playwright/test";

const nativeIntegration = {
  provider: "native",
  public_url: null,
  entry_url: "/community",
  available: true,
  sso_enabled: false,
  setup_required: false,
};


test("answers a question, votes, accepts an answer, and joins the discussion", async ({ page }) => {
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
    answer_count: 1,
    accepted_answer_id: null as string | null,
    viewer_vote: 0,
    created_at: now,
    updated_at: now,
  };
  const answers = [{
    id: "answer-first",
    post_id: post.id,
    author_user_id: "user-answerer",
    author_display_name: "学习者乙",
    body: "先用自己的话复述，再用一个新例子和一个反例检查边界。",
    vote_score: 2,
    viewer_vote: 0,
    is_accepted: false,
    author_reputation: 30,
    created_at: now,
    updated_at: now,
  }];
  const comments: Array<Record<string, unknown>> = [];

  await page.context().addCookies([
    { name: "openclass.auth.token", value: "community-test-token", domain: "127.0.0.1", path: "/" },
  ]);

  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      id: "user-author",
      email: "author@example.com",
      role: "user",
      display_name: "学习者甲",
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

    if (path === "/api/community/integration" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(nativeIntegration) });
      return;
    }
    if (path === "/api/community/spaces" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([space]) });
      return;
    }
    if (path === "/api/community/posts" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([post]) });
      return;
    }
    if (path === `/api/community/posts/${post.id}` && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ post, answers, comments }) });
      return;
    }
    if (path === `/api/community/posts/${post.id}` && method === "PUT") {
      const payload = request.postDataJSON() as { title: string; body: string; tags: string[] };
      post.title = payload.title;
      post.body = payload.body;
      post.tags = payload.tags;
      post.updated_at = "2026-07-24T11:00:00+00:00";
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(post) });
      return;
    }
    if (path === `/api/community/posts/${post.id}/vote` && method === "PUT") {
      post.viewer_vote = 1;
      post.vote_score = 4;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ post_id: post.id, viewer_vote: 1, vote_score: 4 }) });
      return;
    }
    if (path === `/api/community/answers/${answers[0].id}/vote` && method === "PUT") {
      answers[0].viewer_vote = 1;
      answers[0].vote_score = 3;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ answer_id: answers[0].id, viewer_vote: 1, vote_score: 3 }) });
      return;
    }
    if (path === `/api/community/posts/${post.id}/accepted-answer` && method === "PUT") {
      const payload = request.postDataJSON() as { answer_id: string | null };
      post.accepted_answer_id = payload.answer_id;
      answers.forEach((answer) => { answer.is_accepted = answer.id === payload.answer_id; });
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ post_id: post.id, accepted_answer_id: payload.answer_id }) });
      return;
    }
    if (path === `/api/community/posts/${post.id}/answers` && method === "POST") {
      const payload = request.postDataJSON() as { body: string };
      const answer = { id: "answer-new", post_id: post.id, author_user_id: "user-author", author_display_name: "学习者甲", body: payload.body, vote_score: 0, viewer_vote: 0, is_accepted: false, author_reputation: 0, created_at: now, updated_at: now };
      answers.push(answer);
      post.answer_count += 1;
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(answer) });
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

  await page.getByRole("button", { name: "编辑", exact: true }).first().click();
  await page.getByLabel("编辑帖子标题").fill("如何形成可验证、可复用的理解？");
  const postEditor = page.getByLabel("编辑帖子正文").locator("..");
  await page.getByLabel("编辑帖子正文").fill("先写出 **判断依据**，再让其他人复现。\n\n```text\n输入 → 推理 → 结论\n```");
  await postEditor.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.getByText("判断依据", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByRole("heading", { name: "如何形成可验证、可复用的理解？" })).toBeVisible();

  await page.getByRole("button", { name: "赞同帖子", exact: true }).click();
  await expect(page.getByText("4", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "赞同 学习者乙 的回答", exact: true }).click();
  await expect(page.getByText("3", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "采纳 学习者乙 的回答" }).click();
  await expect(page.getByText("提问者已采纳")).toBeVisible();

  await page.getByLabel("写回答").fill("我还会让别人根据我的解释复现同一个判断过程。");
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.getByText("我还会让别人根据我的解释复现同一个判断过程。")).toBeVisible();

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
    if (path === "/api/community/integration" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(nativeIntegration) });
      return;
    }
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
      const post = { id: "post-new", community_id: "community-new", community_slug: payload.community_slug, community_name: "知识可视化", author_user_id: "user-author", author_display_name: "发布者", post_type: payload.post_type, title: payload.title, body: payload.body, tags: payload.tags, vote_score: 0, comment_count: 0, answer_count: 0, accepted_answer_id: null, viewer_vote: 0, created_at: now, updated_at: now };
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
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(nativeIntegration),
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


test("shows the Answer gateway when the external provider is ready", async ({ page }) => {
  const now = "2026-07-25T10:00:00+00:00";
  await page.context().addCookies([
    { name: "openclass.auth.token", value: "community-test-token", domain: "127.0.0.1", path: "/" },
  ]);
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ id: "user-author", email: "author@example.com", role: "user", display_name: "学习者甲", avatar_url: null, created_at: now, last_login_at: now, auth_identities: [] }),
  }));
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      provider: "answer",
      public_url: "https://community.example.com",
      entry_url: "https://community.example.com/answer/api/v1/connector/login/basic",
      available: true,
      sso_enabled: true,
      setup_required: false,
    }),
  }));
  await page.route("**/api/community/spaces**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/api/community/posts**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));

  await page.goto("/community");

  await expect(page.getByRole("heading", { name: "进入知识问答社区" })).toBeVisible();
  await expect(page.getByText("社区服务和单点登录已就绪")).toBeVisible();
  await expect(page.getByRole("button", { name: "进入社区" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "继续使用内置社区" })).toBeVisible();
});
