import { expect, test, type Page } from "@playwright/test";

const user = {
  id: "reviewer",
  email: "reviewer@example.com",
  role: "user",
  display_name: "课程作者",
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
    title: "协作课程",
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
    source_title: "公开课程",
    title: "补充关键背景",
    description: "这个版本补充了学习过程中发现的必要背景。",
    status: "open",
    version: 1,
    current_revision: 1,
    source_author: { user_id: "reviewer", display_name: "课程作者", avatar_url: null },
    contributor: { user_id: "learner", display_name: "学习者", avatar_url: null },
    revision: {
      id: "revision_browser",
      revision_number: 1,
      source_commit_id: "commit_base",
      base_document: boardDocument("document_base", "第一节\n原始解释"),
      proposed_document: boardDocument("document_proposal", "第一节\n改进后的解释\n新增示例"),
      created_at: "2026-07-27T01:00:00+00:00",
    },
    events: [
      {
        id: "event_opened",
        contribution_id: "contribution_browser",
        kind: "opened",
        actor: { user_id: "learner", display_name: "学习者", avatar_url: null },
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

async function authenticate(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("openclass.auth.token", "browser-test-token");
    document.cookie = "openclass.auth.token=browser-test-token; Path=/; SameSite=Lax";
  });
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
  await expect(page.getByRole("heading", { name: "课程协作" })).toBeVisible();
  await expect(page.getByRole("link", { name: /补充关键背景/ })).toBeVisible();
  await page.getByRole("button", { name: "我提交的" }).click();
  await expect(page.getByText("当前筛选下还没有课程改进方案。")).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "补充关键背景" })).toBeVisible();
  await expect(page.getByText("来源基线")).toBeVisible();
  await expect(page.getByText("贡献版本")).toBeVisible();
  await expect(page.getByText("新增示例")).toBeVisible();
  await expect(page.getByRole("link", { name: "登录正式账号" })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始合并" })).toHaveCount(0);
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
  await page.getByPlaceholder("参与这次课程改进讨论").fill("这项修改可以进入合并审查");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("这项修改可以进入合并审查")).toBeVisible();

  const mergeRequest = page.waitForRequest("**/api/contributions/contribution_browser/merge/start");
  await page.getByRole("button", { name: "开始合并" }).click();
  await mergeRequest;
  await expect(page).toHaveURL(/\/studio\?lesson=lesson_source&contribution=contribution_browser/);
});
