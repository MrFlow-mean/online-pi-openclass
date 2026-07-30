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

const pageSettings = {
  margin_preset: "normal",
  orientation: "portrait",
  page_size: "a4",
  columns: 1,
  page_border: false,
  background_style: "plain",
  watermark_text: "",
  line_numbers: false,
  show_page_number: true,
  header_text: "",
  footer_text: "",
};

const copiedDocument = {
  id: "document_personal_copy",
  title: "真实公开课程",
  content_json: {
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "公开课程讲义" }] }],
  },
  content_html: "<p>公开课程讲义</p>",
  content_text: "公开课程讲义",
  page_settings: pageSettings,
};

const copiedLesson = {
  id: "lesson_personal_copy",
  title: "真实公开课程",
  slug: "real-public-course-copy",
  summary: "这条结果来自课程搜索 API。",
  tags: ["公开", "可检索"],
  visibility: "private",
  publication_review: {
    id: "review_personal_copy",
    status: "not_started",
    source_fingerprint: "",
    scanned_source_count: 0,
    scanned_unit_count: 0,
    findings: [],
    message: "",
  },
  published_version: null,
  board_document: copiedDocument,
  learning_requirements: null,
  board_task_requirements: null,
  history_graph: {
    current_branch: "main",
    branches: {
      main: {
        name: "main",
        head_commit_id: "commit_personal_copy",
        base_commit_id: "commit_personal_copy",
        created_at: "2026-07-28T01:00:00+00:00",
      },
    },
    commits: [
      {
        id: "commit_personal_copy",
        label: "Personal copy baseline",
        message: "Saved a private, editable copy of a public lesson",
        branch_name: "main",
        created_at: "2026-07-28T01:00:00+00:00",
        parent_ids: [],
        operations: [],
        snapshot: copiedDocument,
        metadata: {
          kind: "initial_document",
          history_node_kind: "system",
          published_conversation: [
            { role: "user", content: "公开课程中的原问题" },
            { role: "assistant", content: "公开课程中的原回答" },
          ],
        },
      },
    ],
  },
  created_at: "2026-07-28T01:00:00+00:00",
  updated_at: "2026-07-28T01:00:00+00:00",
};

const copiedPackage = {
  ...workspace.packages[0],
  lessons: [copiedLesson],
  open_lesson_ids: [copiedLesson.id],
  active_lesson_id: copiedLesson.id,
  workspace_tab_order: [copiedLesson.id],
};

const copiedWorkspace = {
  active_package_id: copiedPackage.id,
  packages: [copiedPackage],
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
  let downloadedLessonId = "";
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(downloadedLessonId ? copiedWorkspace : workspace),
    }),
  );
  await page.route("**/api/course-package", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(copiedPackage),
    }),
  );
  await page.route("**/api/contributions?*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  let requestedQuery = "";
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
            star_count: 0,
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
            star_count: 0,
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
      body: JSON.stringify(copiedPackage),
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
  await expect(page.getByRole("link", { name: "真实公开课程", exact: true })).toBeVisible();
  expect(requestedQuery).toBe("真实内容");
  await expect(page.getByText("公开课作者")).toBeVisible();
  await expect(page.getByRole("link", { name: "详情 真实公开课程" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 真实公开课程" })).toBeVisible();
  await expect(page.getByText("筛选")).toHaveCount(0);
  await expect(page.getByText("排序方式")).toHaveCount(0);
  await page.getByRole("button", { name: "收藏 真实公开课程" }).click();
  await expect.poll(() => starredLessonId).toBe("lesson_public_search");
  await expect(page.getByRole("button", { name: "取消收藏 真实公开课程" })).toBeVisible();

  await page.getByRole("button", { name: "退出搜索" }).click();
  await expect(page.getByLabel("添加课程包")).toBeVisible();

  await search.click();
  await search.fill("真实内容");
  await page.getByRole("button", { name: "下载 真实公开课程" }).click();
  await expect.poll(() => downloadedLessonId).toBe("lesson_public_search");
});

test("downloaded public course conversations render in the personal studio", async ({
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
  await page.route("**/api/course-package", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(copiedPackage),
    }),
  );
  await page.route("**/api/contributions?*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  await page.goto("/studio");
  await expect(page.getByText("公开课程中的原问题", { exact: true })).toBeVisible();
  await expect(page.getByText("公开课程中的原回答", { exact: true })).toBeVisible();
});
