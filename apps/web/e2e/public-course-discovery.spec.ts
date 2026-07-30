import { expect, test } from "@playwright/test";

const publicCourses = [
  {
    id: "lesson_discovery",
    kind: "lesson",
    owner_display_name: "真实课程作者",
    owner_avatar_url: null,
    title: "公开课程动态项目",
    summary: "来自真实公开课程发现接口，可查看详情并下载到个人项目。",
    tags: ["公开", "协作"],
    lesson_count: 1,
    updated_at: new Date().toISOString(),
    visibility: "public",
    is_starred: false,
    star_count: 7,
  },
  {
    id: "package_discovery",
    kind: "package",
    owner_display_name: "课程包作者",
    owner_avatar_url: null,
    title: "可下载课程包",
    summary: "下载后保留为私有课程包。",
    tags: ["课程包"],
    lesson_count: 3,
    updated_at: new Date(Date.now() - 3_600_000).toISOString(),
    visibility: "public",
    is_starred: false,
    star_count: 3,
  },
];

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([
    {
      name: "openclass.auth.token",
      value: "public-discovery-token",
      domain: "127.0.0.1",
      path: "/",
      sameSite: "Lax",
    },
  ]);
  await page.addInitScript(() => {
    window.localStorage.setItem("openclass.auth.token", "public-discovery-token");
    document.cookie = "openclass.auth.token=public-discovery-token; Path=/; SameSite=Lax";
  });
  await page.route("**/api/public/courses?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(publicCourses),
    }),
  );
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "discovery-viewer",
        email: "discovery-viewer@example.com",
        role: "user",
        display_name: "课程发现用户",
        avatar_url: null,
        created_at: "2026-07-30T00:00:00+00:00",
        last_login_at: null,
        auth_identities: [],
      }),
    }),
  );
});

test("popular cards expose details and download actions from real public data", async ({
  page,
}) => {
  let downloadedLessonId = "";
  await page.route("**/api/public/lessons/lesson_discovery/fork", (route) => {
    downloadedLessonId = "lesson_discovery";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
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
        active_lesson_id: "lesson_personal_copy",
        workspace_tab_order: [],
      }),
    });
  });

  await page.goto("/trending");
  await expect(page.getByRole("heading", { name: "热门项目" })).toBeVisible();
  await expect(page.getByText("真实公开课程", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "详情 公开课程动态项目" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 公开课程动态项目" })).toBeVisible();
  await expect(page.getByRole("link", { name: "详情 可下载课程包" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 可下载课程包" })).toBeVisible();

  await page.getByRole("button", { name: "下载 公开课程动态项目" }).click();
  await expect.poll(() => downloadedLessonId).toBe("lesson_discovery");
});

test("activity cards expose details and download actions from real public data", async ({
  page,
}) => {
  let downloadedPackageId = "";
  await page.route("**/api/public/packages/package_discovery/fork", (route) => {
    downloadedPackageId = "package_discovery";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "package_personal_copy",
        title: "可下载课程包",
        summary: "",
        visibility: "private",
        publication_review: {
          id: "review_package",
          status: "not_started",
          source_fingerprint: "",
          scanned_source_count: 0,
          scanned_unit_count: 0,
          findings: [],
          message: "",
        },
        is_standalone: false,
        lessons: [],
        course_graph: [],
        resources: [],
        open_lesson_ids: [],
        active_lesson_id: "lesson_package_personal_copy",
        workspace_tab_order: [],
      }),
    });
  });

  await page.goto("/following");
  await expect(page.getByRole("heading", { name: "课程动态" })).toBeVisible();
  await expect(page.getByText("2 个真实项目")).toBeVisible();
  await expect(page.getByRole("link", { name: "详情 公开课程动态项目" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 公开课程动态项目" })).toBeVisible();
  await expect(page.getByRole("link", { name: "详情 可下载课程包" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 可下载课程包" })).toBeVisible();
  await page.getByRole("button", { name: "下载 可下载课程包" }).click();
  await expect.poll(() => downloadedPackageId).toBe("package_discovery");
});
