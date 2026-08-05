import { expect, test } from "@playwright/test";

const publicCourses = [
  {
    id: "lesson_discovery",
    kind: "lesson",
    owner_display_name: "real course author",
    owner_avatar_url: null,
    title: "Open course dynamic projects",
    summary: "From the real public course discovery interface, you can view details and download to personal projects.",
    tags: ["public", "cooperation"],
    lesson_count: 1,
    updated_at: new Date().toISOString(),
    visibility: "public",
    is_starred: false,
    star_count: 7,
  },
  {
    id: "package_discovery",
    kind: "package",
    owner_display_name: "Course Pack Author",
    owner_avatar_url: null,
    title: "Downloadable course packs",
    summary: "After downloading, keep it as a private course package.",
    tags: ["course package"],
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
        display_name: "course discovery user",
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
        active_lesson_id: "lesson_personal_copy",
        workspace_tab_order: [],
      }),
    });
  });

  await page.goto("/trending");
  await expect(page.getByRole("heading", { name: "Popular items" })).toBeVisible();
  await expect(page.getByText("Real public courses", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Details Open course dynamic projects" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Open course dynamic projects" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Details Downloadable course packs" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Downloadable course packs" })).toBeVisible();

  await page.getByRole("button", { name: "Download Open course dynamic projects" }).click();
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
        title: "Downloadable course packs",
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
  await expect(page.getByRole("heading", { name: "Course dynamics" })).toBeVisible();
  await expect(page.getByText("2 real projects")).toBeVisible();
  await expect(page.getByRole("link", { name: "Details Open course dynamic projects" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Open course dynamic projects" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Details Downloadable course packs" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Downloadable course packs" })).toBeVisible();
  await page.getByRole("button", { name: "Download Downloadable course packs" }).click();
  await expect.poll(() => downloadedPackageId).toBe("package_discovery");
});
