import { expect, test } from "@playwright/test";

const workspace = {
  active_package_id: "package_standalone",
  packages: [
    {
      id: "package_standalone",
      title: "individual courses",
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
  title: "Real public courses",
  content_json: {
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "Open course handouts" }] }],
  },
  content_html: "<p>Public course handouts</p>",
  content_text: "Open course handouts",
  page_settings: pageSettings,
};

const copiedLesson = {
  id: "lesson_personal_copy",
  title: "Real public courses",
  slug: "real-public-course-copy",
  summary: "This result comes from the course search API.",
  tags: ["public", "Searchable"],
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
            { role: "user", content: "Original question in public course" },
            { role: "assistant", content: "Original answer in public course" },
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
        display_name: "Search users",
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
            owner_display_name: "Search users",
            owner_avatar_url: null,
            title: "My private course",
            summary: "This personal course can be searched by the current user.",
            tags: ["personal"],
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
            owner_display_name: "Open class author",
            owner_avatar_url: null,
            title: "Real public courses",
            summary: "This result comes from the course search API.",
            tags: ["public", "Searchable"],
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
  await expect(page.getByLabel("Add course package")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open credits and top-up" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open GitHub repository" })).toBeVisible();

  const search = page.getByPlaceholder(/Search open courses/i);
  await search.click();

  await expect(page.getByLabel("Add course package")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Open credits and top-up" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Open GitHub repository" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Search courses" })).toBeVisible();

  await search.fill("real content");
  await expect(page.getByRole("heading", { name: "My courses" })).toBeVisible();
  await expect(page.getByRole("button", { name: "My private course" })).toBeVisible();
  await expect(page.getByText("Private", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Public courses" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Real public courses", exact: true })).toBeVisible();
  expect(requestedQuery).toBe("real content");
  await expect(page.getByText("Open class author")).toBeVisible();
  await expect(page.getByRole("link", { name: "Details Real public courses" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Real public courses" })).toBeVisible();
  await expect(page.getByText("filter")).toHaveCount(0);
  await expect(page.getByText("sort by")).toHaveCount(0);
  await page.getByRole("button", { name: "Star Real public courses" }).click();
  await expect.poll(() => starredLessonId).toBe("lesson_public_search");
  await expect(page.getByRole("button", { name: "Unstar Real public courses" })).toBeVisible();

  await page.getByRole("button", { name: "Exit search" }).click();
  await expect(page.getByLabel("Add course package")).toBeVisible();

  await search.click();
  await search.fill("real content");
  await page.getByRole("button", { name: "Download Real public courses" }).click();
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
        display_name: "Search users",
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
  await expect(page.getByText("Original question in public course", { exact: true })).toBeVisible();
  await expect(page.getByText("Original answer in public course", { exact: true })).toBeVisible();
});
