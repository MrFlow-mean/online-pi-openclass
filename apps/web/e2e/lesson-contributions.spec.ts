import { expect, test, type Page } from "@playwright/test";

const user = {
  id: "reviewer",
  email: "reviewer@example.com",
  role: "user",
  display_name: "Course Author",
  avatar_url: null,
  created_at: "2026-07-27T00:00:00+00:00",
  last_login_at: null,
  auth_identities: [],
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

function boardDocument(id: string, text: string) {
  return {
    id,
    title: "Collaborative courses",
    content_json: {
      type: "doc",
      content: [{ type: "paragraph", content: [{ type: "text", text }] }],
    },
    content_html: `<p>${text}</p>`,
    content_text: text,
    page_settings: pageSettings,
  };
}

function contribution(overrides: Record<string, unknown> = {}) {
  return {
    id: "contribution_browser",
    source_lesson_id: "lesson_source",
    viewer_project_lesson_id: "lesson_source",
    source_title: "Open courses",
    title: "Add key background",
    description: "This version supplements the necessary background discovered during the study process.",
    status: "open",
    version: 1,
    current_revision: 1,
    source_author: { user_id: "reviewer", display_name: "Course Author", avatar_url: null },
    contributor: { user_id: "learner", display_name: "learner", avatar_url: null },
    revision: {
      id: "revision_browser",
      revision_number: 1,
      source_commit_id: "commit_base",
      base_document: boardDocument("document_base", "Section 1 original explanation"),
      proposed_document: boardDocument("document_proposal", "Improved explanations and new examples in Section 1"),
      created_at: "2026-07-27T01:00:00+00:00",
    },
    events: [
      {
        id: "event_opened",
        contribution_id: "contribution_browser",
        kind: "opened",
        actor: { user_id: "learner", display_name: "learner", avatar_url: null },
        body: "",
        metadata: { revision_number: 1 },
        created_at: "2026-07-27T01:00:00+00:00",
      },
    ],
    viewer_permissions: {
      can_comment: true,
      can_update: false,
      can_close: true,
      can_reopen: false,
      can_start_merge: true,
      can_return_for_changes: false,
    },
    source_is_public: true,
    merge_session_id: null,
    merged_commit_id: null,
    created_at: "2026-07-27T01:00:00+00:00",
    updated_at: "2026-07-27T01:00:00+00:00",
    closed_at: null,
    ...overrides,
  };
}

function workspaceWithProject() {
  return {
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
        lessons: [
          {
            id: "lesson_source",
            title: "Open courses",
            slug: "public-course",
            summary: "Courses for project-level collaboration management.",
            tags: [],
            visibility: "public",
            publication_review: {
              id: "review_lesson",
              status: "approved",
              source_fingerprint: "",
              scanned_source_count: 0,
              scanned_unit_count: 0,
              findings: [],
              message: "Can be made public.",
            },
            board_document: boardDocument("document_source", "Section 1"),
            history_graph: {
              branches: {},
              commits: [],
              current_branch: "main",
            },
            created_at: "2026-07-27T00:00:00+00:00",
            updated_at: "2026-07-27T01:00:00+00:00",
          },
        ],
        course_graph: [],
        resources: [],
        open_lesson_ids: [],
        active_lesson_id: "lesson_source",
        workspace_tab_order: [],
      },
    ],
  };
}

async function authenticate(page: Page) {
  await page.context().addCookies([
    {
      name: "openclass.auth.token",
      value: "browser-test-token",
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user) })
  );
}

test("lists received and submitted lesson contributions", async ({ page }) => {
  await authenticate(page);
  await page.route("**/api/contributions?role=received", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([contribution()]) })
  );
  await page.route("**/api/contributions?role=submitted", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );

  await page.goto("/contributions");
  await expect(page.getByRole("heading", { name: "Course Collaboration" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Add key background/ })).toBeVisible();
  await page.getByRole("button", { name: "I submitted" }).click();
  await expect(page.getByText("There are no course improvement plans under the current filter.")).toBeVisible();
});

test("keeps the profile free of the removed collaboration panel", async ({ page }) => {
  await authenticate(page);
  await page.route("**/api/workspace", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(workspaceWithProject()) })
  );

  await page.goto("/");
  await expect(page.getByRole("link", { name: "Open course collaboration" })).toHaveCount(0);

  await page.goto("/profile?tab=collaboration");
  await expect(page).toHaveURL(/tab=repositories/);
  await expect(page.getByRole("button", { name: "cooperation" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Project collaboration" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Open .+ collaboration management/ })).toHaveCount(0);
});

test("shows a public diff without exposing write controls", async ({ page }) => {
  const publicView = contribution({
    viewer_permissions: {
      can_comment: false,
      can_update: false,
      can_close: false,
      can_reopen: false,
      can_start_merge: false,
      can_return_for_changes: false,
    },
  });
  await page.route("**/api/public/contributions/contribution_browser", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(publicView) })
  );

  await page.goto("/contributions/contribution_browser");
  await expect(page.getByRole("heading", { name: "Add key background" })).toBeVisible();
  await expect(page.getByText("source baseline")).toBeVisible();
  await expect(page.getByText("Contributed version")).toBeVisible();
  await expect(page.getByText("New example")).toBeVisible();
  await expect(page.getByRole("link", { name: "Log in to official account" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start merging" })).toHaveCount(0);
});

test("comments and starts a merge from the contribution detail", async ({ page }) => {
  await authenticate(page);
  let current = contribution();
  await page.route("**/api/contributions/contribution_browser", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) })
  );
  await page.route("**/api/contributions/contribution_browser/comments", async (route) => {
    const payload = route.request().postDataJSON() as { body: string };
    current = contribution({
      version: 2,
      events: [
        ...current.events,
        {
          id: "comment_browser",
          contribution_id: current.id,
          kind: "commented",
          actor: { user_id: user.id, display_name: user.display_name, avatar_url: null },
          body: payload.body,
          metadata: {},
          created_at: "2026-07-27T02:00:00+00:00",
        },
      ],
    });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
  });
  await page.route("**/api/contributions/contribution_browser/merge/start", async (route) => {
    current = contribution({ status: "merge_draft", version: 3, merge_session_id: "merge_browser" });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
  });

  await page.goto("/contributions/contribution_browser");
  await page.getByPlaceholder("Participate in this course improvement discussion").fill("This modification can go to merge review");
  await page.getByRole("button", { name: "send" }).click();
  await expect(page.getByText("This modification can go to merge review")).toBeVisible();

  const mergeRequest = page.waitForRequest("**/api/contributions/contribution_browser/merge/start");
  await page.getByRole("button", { name: "Start merging" }).click();
  await mergeRequest;
  await expect(page).toHaveURL(/\/studio\?lesson=lesson_source&contribution=contribution_browser/);
});
