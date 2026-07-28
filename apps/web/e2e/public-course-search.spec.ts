import { expect, test } from "@playwright/test";

const workspace = {
  active_package_id: "package_standalone",
  packages: [
    {
      id: "package_standalone",
      title: "单独课程",
      summary: "",
      visibility: "private",
      publication_review: {
        id: "review_standalone",
        status: "not_started",
        source_fingerprint: "",
        scanned_source_count: 0,
        scanned_unit_count: 0,
        findings: [],
        message: "",
      },
      is_standalone: true,
      lessons: [],
      course_graph: [],
      resources: [],
      open_lesson_ids: [],
      active_lesson_id: null,
      workspace_tab_order: [],
    },
  ],
};

test("search mode hides the home chrome and groups owned and public course results", async ({
  context,
  page,
}) => {
  await context.addCookies([
    {
      name: "openclass.auth.token",
      value: "public-search-token",
      domain: "127.0.0.1",
      path: "/",
      sameSite: "Lax",
    },
  ]);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "searcher",
        email: "searcher@example.com",
        role: "user",
        display_name: "搜索用户",
        avatar_url: null,
        created_at: "2026-07-28T00:00:00+00:00",
        last_login_at: null,
        auth_identities: [],
      }),
    }),
  );
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(workspace),
    }),
  );
  await page.route("**/api/contributions?*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  let requestedQuery = "";
  let downloadedLessonId = "";
  let starredLessonId = "";
  await page.route("**/api/courses/search?*", (route) => {
    requestedQuery = new URL(route.request().url()).searchParams.get("q") ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        owned_courses: [
          {
            id: "lesson_owned_search",
            kind: "lesson",
            owner_display_name: "搜索用户",
            owner_avatar_url: null,
            title: "我的私有课程",
            summary: "这条本人课程可以被当前用户搜索到。",
            tags: ["个人"],
            lesson_count: 1,
            updated_at: "2026-07-28T02:00:00+00:00",
            visibility: "private",
            is_starred: false,
          },
        ],
        public_courses: [
          {
            id: "lesson_public_search",
            kind: "lesson",
            owner_display_name: "公开课作者",
            owner_avatar_url: null,
            title: "真实公开课程",
            summary: "这条结果来自课程搜索 API。",
            tags: ["公开", "可检索"],
            lesson_count: 1,
            updated_at: "2026-07-28T01:00:00+00:00",
            visibility: "public",
            is_starred: false,
          },
        ],
      }),
    });
  });
  await page.route("**/api/public/lessons/lesson_public_search/fork", (route) => {
    downloadedLessonId = "lesson_public_search";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...workspace.packages[0],
        active_lesson_id: "lesson_personal_copy",
      }),
    });
  });
  await page.route("**/api/public/courses/lesson/lesson_public_search/star", (route) => {
    starredLessonId = "lesson_public_search";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "lesson_public_search",
        kind: "lesson",
        is_starred: true,
      }),
    });
  });

  await page.goto("/");
  await expect(page.getByLabel("添加课程包")).toBeVisible();
  await expect(page.getByRole("link", { name: "打开积分与充值" })).toBeVisible();
  await expect(page.getByRole("link", { name: "打开 GitHub 仓库" })).toBeVisible();

  const search = page.getByPlaceholder(/搜索.*课程/);
  await search.click();

  await expect(page.getByLabel("添加课程包")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "打开积分与充值" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "打开 GitHub 仓库" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "搜索课程" })).toBeVisible();

  await search.fill("真实内容");
  await expect(page.getByRole("heading", { name: "我的课程" })).toBeVisible();
  await expect(page.getByRole("button", { name: "我的私有课程" })).toBeVisible();
  await expect(page.getByText("私有", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "其他用户的公开课程" })).toBeVisible();
  await expect(page.getByRole("link", { name: "真实公开课程" })).toBeVisible();
  expect(requestedQuery).toBe("真实内容");
  await expect(page.getByText("公开课作者")).toBeVisible();
  await expect(page.getByText("筛选")).toHaveCount(0);
  await expect(page.getByText("排序方式")).toHaveCount(0);
  await page.getByRole("button", { name: "收藏 真实公开课程" }).click();
  await expect.poll(() => starredLessonId).toBe("lesson_public_search");
  await expect(page.getByRole("button", { name: "取消收藏 真实公开课程" })).toBeVisible();

  await page.getByRole("button", { name: "退出搜索" }).click();
  await expect(page.getByLabel("添加课程包")).toBeVisible();

  await search.click();
  await search.fill("真实内容");
  await page.getByRole("button", { name: "下载 真实公开课程 并开学" }).click();
  await expect.poll(() => downloadedLessonId).toBe("lesson_public_search");
});
