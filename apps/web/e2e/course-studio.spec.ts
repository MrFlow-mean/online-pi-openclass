import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import os from "node:os";
import { resolve } from "node:path";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8110";
const ROOT_DIR = resolve(process.cwd(), "../..");
const E2E_DATABASE_PATH = resolve(os.tmpdir(), "openclass-e2e/openclass.sqlite3");

test.beforeEach(async ({ page }) => {
  const textModel = {
    provider: "openai_codex",
    model: "gpt-5.5",
    access_method: "chatgpt_subscription",
    label: "OpenAI Codex test model",
    capability: "text",
    enabled: true,
    configured: true,
    default: true,
    default_reasoning_effort: null,
    supported_reasoning_efforts: [],
    default_service_tier: null,
    service_tiers: [],
  };
  const realtimeModel = {
    provider: "openai_codex",
    model: "realtime-unavailable",
    access_method: "chatgpt_subscription",
    label: "Realtime unavailable in browser tests",
    capability: "realtime",
    enabled: false,
    configured: false,
    default: true,
    default_reasoning_effort: null,
    supported_reasoning_efforts: [],
    default_service_tier: null,
    service_tiers: [],
  };
  await page.route("**/api/ai-models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: [textModel],
        realtime: [realtimeModel],
        defaults: {
          text: { provider: textModel.provider, model: textModel.model },
          realtime: { provider: realtimeModel.provider, model: realtimeModel.model },
        },
      }),
    });
  });
});

async function enterAsGuest(page: Page, nextPath = "/") {
  await page.goto(`/login?next=${encodeURIComponent(nextPath)}`);
  await page.getByRole("button", { name: /Continue as guest/ }).click();
  await expect(page).toHaveURL(/\/studio$/);
}

async function enterAsGuestThroughApi(page: Page) {
  const response = await page.request.post(`${API_BASE_URL}/api/auth/guest`);
  expect(response.ok()).toBeTruthy();
  const session = (await response.json()) as { token: string };
  await page.goto("/");
  await page.evaluate((token) => {
    window.sessionStorage.setItem("openclass.guest.auth.token", token);
    window.localStorage.setItem("openclass.connected-guest.auth.token", token);
    document.cookie = `openclass.guest.auth.token=${encodeURIComponent(token)}; Path=/; SameSite=Lax`;
  }, session.token);
  await page.goto("/studio");
  await expect(page).toHaveURL(/\/studio$/);
}

async function createPackageFromHome(page: Page, title: string) {
  const guestToken = await page.evaluate(() =>
    window.sessionStorage.getItem("openclass.guest.auth.token")
  );
  const response = await page.request.post(`${API_BASE_URL}/api/packages`, {
    headers: guestToken ? { Authorization: `Bearer ${guestToken}` } : undefined,
    data: { title, summary: "" },
  });
  expect(response.ok()).toBeTruthy();
  await page.goto("/studio");
  await expect(page).toHaveURL(/\/studio$/);
}

async function enterAsMemberThroughApi(page: Page) {
  const guestResponse = await page.request.post(`${API_BASE_URL}/api/auth/guest`);
  expect(guestResponse.ok()).toBeTruthy();
  const guestSession = (await guestResponse.json()) as { token: string };
  const pythonExecutable = process.env.OPENCLASS_E2E_PYTHON
    ? resolve(ROOT_DIR, process.env.OPENCLASS_E2E_PYTHON)
    : resolve(ROOT_DIR, ".venv/bin/python");
  const subject = `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const script = [
    "import sys",
    "from pathlib import Path",
    "sys.path.insert(0, str(Path.cwd() / 'apps/api'))",
    "from app.services.auth_service import AuthService, OAuthProfile",
    "auth = AuthService(Path(sys.argv[1]))",
    "token, _ = auth.login_with_oauth(",
    "    OAuthProfile(provider='e2e', subject=sys.argv[3], email=f'{sys.argv[3]}@example.com', display_name='E2E member'),",
    "    guest_session_token=sys.argv[2],",
    ")",
    "print(token)",
  ].join("\n");
  const memberToken = execFileSync(
    pythonExecutable,
    ["-c", script, E2E_DATABASE_PATH, guestSession.token, subject],
    { cwd: ROOT_DIR, encoding: "utf8" }
  ).trim();
  await page.context().addCookies([
    {
      name: "openclass.auth.token",
      value: memberToken,
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  await page.goto("/");
  await expect(page.getByLabel("Add course package")).toBeVisible();
  return memberToken;
}

async function nameNextGeneratedLessonForTest(page: Page, title: string) {
  await page.route(
    "**/api/lessons/generate*",
    async (route) => {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      await route.continue({
        postData: JSON.stringify({ ...payload, topic: title }),
        headers: {
          ...route.request().headers(),
          "content-type": "application/json",
        },
      });
    },
    { times: 1 }
  );
}

async function createLessonFromEmptyStudio(page: Page, title: string) {
  await page.goto("/studio");
  await expect(page.getByText("This package is empty")).toBeVisible();
  await nameNextGeneratedLessonForTest(page, title);
  await page.getByRole("button", { name: "Create first page" }).click();
  await expect(page.getByRole("button", { name: `${title} main` })).toBeVisible();
  await expect(page.locator(".ProseMirror")).toBeVisible();
}

async function createLessonFromTabBar(page: Page, title: string) {
  await nameNextGeneratedLessonForTest(page, title);
  await page.getByLabel("Create page").click();
  await expect(page.getByRole("button", { name: `${title} main` })).toBeVisible();
}

async function setInterfaceLanguage(page: Page, interfaceLanguage: "zh-CN" | "en") {
  await page.evaluate((nextLanguage) => {
    const key = "openclass.profile.settings";
    const eventName = "openclass.profile.settings.changed";
    const stored = window.localStorage.getItem(key);
    const current = stored ? (JSON.parse(stored) as Record<string, unknown>) : {};
    const nextSettings = { ...current, interfaceLanguage: nextLanguage };
    window.localStorage.setItem(key, JSON.stringify(nextSettings));
    window.dispatchEvent(new CustomEvent(eventName, { detail: nextSettings }));
  }, interfaceLanguage);
}

async function writeEditorTextAndWaitForSave(page: Page, text: string) {
  const editor = page.locator(".ProseMirror").first();
  const saveResponse = page.waitForResponse(
    (response) => response.url().includes("/document/save") && response.request().method() === "POST"
  );
  await editor.click();
  await editor.fill(text);
  await saveResponse;
  await expect(editor).toContainText(text);
}

async function openHistoryPanel(page: Page) {
  await page
    .getByRole("button", { name: /Expand right sidebar|Expand right column|展开右侧栏/ })
    .click();
  await expect(page.getByText("Revision history")).toBeVisible();
}

test("creates a package and lesson, edits the document, and persists a version", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `主流程页面 ${unique}`;
  const documentText = `第一版讲义内容 ${unique} · 完成度 100%`;
  await enterAsGuest(page);
  await createPackageFromHome(page, `维护性测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, lessonTitle);

  await expect(page.getByRole("button", { name: "Ask Mode" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Agent Edit Mode" })).toHaveCount(0);

  await writeEditorTextAndWaitForSave(page, documentText);
  await openHistoryPanel(page);

  const historyNode = page.locator("[data-history-node-id]").filter({ hasText: "Auto Save" });
  await expect(historyNode).toHaveCount(1);
  await expect(historyNode.getByRole("button", { name: "Preview" })).toBeVisible();
  await expect(historyNode.getByRole("button", { name: "Restore" })).toBeVisible();
  await expect(historyNode.getByRole("button", { name: "Branch" })).toBeVisible();
  await historyNode.getByRole("button", { name: "Reference to input box" }).click();
  await expect(page.getByText("Quote 1 · Conversation Quote")).toBeVisible();
  await expect(page.getByText(/历史节点：Auto Save 类型：Document/)).toBeVisible();

  await expect(historyNode.getByRole("link", { name: "Share to community" })).toHaveCount(0);
  await expect(page.getByText("Course Collaboration", { exact: true })).toHaveCount(0);
});

test("uses lesson deltas for create save close reopen and delete without workspace reloads", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `Delta lifecycle ${unique}`;
  const documentText = `Delta persisted content ${unique}`;
  await enterAsMemberThroughApi(page);
  let workspaceRequestCount = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/workspace") {
      workspaceRequestCount += 1;
    }
  });
  await page.waitForLoadState("networkidle");
  workspaceRequestCount = 0;

  await page.getByLabel("Add standalone lesson").click();
  await nameNextGeneratedLessonForTest(page, lessonTitle);
  const generateResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/lessons/generate" &&
      new URL(response.url()).searchParams.get("response_mode") === "delta"
  );
  await page.getByRole("menuitem", { name: "Create course" }).click();
  expect((await generateResponse).ok()).toBeTruthy();
  await expect(page.getByRole("button", { name: `${lessonTitle} main` })).toBeVisible();
  expect(workspaceRequestCount).toBe(0);

  const saveResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.includes("/document/save") &&
      new URL(response.url()).searchParams.get("response_mode") === "delta"
  );
  await page.locator(".ProseMirror").first().fill(documentText);
  expect((await saveResponse).ok()).toBeTruthy();
  expect(workspaceRequestCount).toBe(0);

  const lessonTab = page.getByRole("button", { name: `${lessonTitle} main` });
  const closeResponse = page.waitForResponse(
    (response) =>
      /\/api\/lessons\/[^/]+\/close$/.test(new URL(response.url()).pathname) &&
      new URL(response.url()).searchParams.get("response_mode") === "delta"
  );
  await lessonTab.locator("span").last().evaluate((element) => (element as HTMLElement).click());
  expect((await closeResponse).ok()).toBeTruthy();
  await expect(page.locator(".ProseMirror")).toHaveCount(0);
  expect(workspaceRequestCount).toBe(0);

  const homeWorkspace = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/workspace"
  );
  await page.goto("/home");
  await homeWorkspace;
  workspaceRequestCount = 0;
  const lessonCard = page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle });
  await expect(lessonCard).toBeVisible();
  await lessonCard.click();
  await expect(page).toHaveURL(/\/studio$/);
  await expect(page.locator(".ProseMirror")).toContainText(documentText);
  expect(workspaceRequestCount).toBe(0);

  const secondHomeWorkspace = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/workspace"
  );
  await page.goto("/home");
  await secondHomeWorkspace;
  workspaceRequestCount = 0;
  const reopenedCard = page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle });
  await reopenedCard.getByLabel("Lesson actions menu").click();
  page.once("dialog", (dialog) => void dialog.accept());
  const deleteResponse = page.waitForResponse(
    (response) =>
      /\/api\/lessons\/[^/]+\/delete$/.test(new URL(response.url()).pathname) &&
      new URL(response.url()).searchParams.get("response_mode") === "delta"
  );
  await page
    .getByRole("button", { name: "Delete", exact: true })
    .evaluate((element) => (element as HTMLButtonElement).click());
  expect((await deleteResponse).ok()).toBeTruthy();
  await expect(reopenedCard).toHaveCount(0);
  expect(workspaceRequestCount).toBe(0);

  await page.reload();
  await expect(page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle })).toHaveCount(0);
});

test("uploads immutable standalone lesson and course package versions", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `可见性单课 ${unique}`;
  const packageTitle = `可见性课程包 ${unique}`;
  const token = await enterAsMemberThroughApi(page);
  const generated = await page.request.post(`${API_BASE_URL}/api/lessons/generate`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { topic: lessonTitle, start_blank: true },
  });
  expect(generated.ok()).toBeTruthy();
  const lesson = (await generated.json()).lessons[0] as {
    id: string;
    history_graph: { commits: Array<{ id: string }> };
  };

  await page.reload();
  const lessonCard = page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle }).first();
  await expect(lessonCard).toBeVisible();
  await lessonCard.getByLabel("Open the course operation menu").click();
  await page.route(
    `**/api/lessons/${lesson.id}/visibility/stream`,
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.continue();
    },
    { times: 1 }
  );
  const lessonVisibilityResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/api/lessons/${lesson.id}/visibility/stream`) && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Upload course", exact: true }).click();
  await expect(lessonCard.getByText("Checking course reference range")).toBeVisible();
  await expect(lessonCard.getByText("Locating materials actually cited in the course")).toBeVisible();
  await expect(lessonCard.getByRole("progressbar", { name: "Course Release Scanning Progress" })).toBeVisible();
  await expect(page.getByText(/AI 正在核对课程实际引用资料的非正文范围/)).toBeVisible();
  expect((await lessonVisibilityResponse).ok()).toBeTruthy();
  await expect(page.getByText("The course has no uploaded materials and can be made public.")).toBeVisible();
  await expect(lessonCard.getByLabel("Uploaded")).toBeVisible();

  const publicLessonPath = `/courses/shared/lesson/${lesson.id}`;
  await page.goto(publicLessonPath);
  await expect(page.getByText(lessonTitle).first()).toBeVisible();
  await expect(page.getByText("Public · Read only")).toBeVisible();

  const historyNodeId = lesson.history_graph.commits[0]?.id;
  expect(historyNodeId).toBeTruthy();
  await page.goto(`${publicLessonPath}?history_node=${encodeURIComponent(historyNodeId!)}`);
  await expect(page.getByText("Currently displayed are the historical nodes where the course is referenced.")).toBeVisible();

  const unavailableNodeResponse = page.waitForResponse(
    (response) => response.url().includes(`/api/public/lessons/${lesson.id}?history_node=missing-node`)
  );
  await page.goto(`${publicLessonPath}?history_node=missing-node`);
  expect((await unavailableNodeResponse).status()).toBe(404);
  await expect(page.getByText(/这个项目不存在/)).toBeVisible();

  await page.goto("/home");
  const blockedLessonCard = page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle }).first();
  await blockedLessonCard.getByLabel("Open the course operation menu").click();
  await page.route(
    `**/api/lessons/${lesson.id}/visibility/stream`,
    async (route) => {
      const authHeader = route.request().headers().authorization;
      const workspaceResponse = await page.request.get(`${API_BASE_URL}/api/workspace`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const workspace = await workspaceResponse.json();
      const targetLesson = workspace.packages
        .flatMap((packageItem: { lessons: Array<Record<string, unknown>> }) => packageItem.lessons)
        .find((item: { id?: unknown }) => item.id === lesson.id);
      if (!targetLesson) {
        throw new Error("Visibility fixture lesson was not found");
      }
      targetLesson.visibility = "private";
      targetLesson.publication_review = {
        id: `publicationreview_${unique}`,
        status: "blocked",
        source_fingerprint: "browser-fixture",
        scanned_source_count: 1,
        scanned_unit_count: 3,
        findings: [
          {
            source_id: "source_fixture",
            source_title: "Upload information.pdf",
            location: "page 2",
            evidence_excerpt: "All rights reserved.",
            reason: "Rights statement in front matter.",
          },
        ],
        message: "A copyright statement was found in the non-text content of the uploaded materials, and the course remains Private.",
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
      };
      const progress = {
        type: "progress",
        progress: {
          stage: "reviewing_units",
          completed_items: 3,
          total_items: 3,
          batch_index: 1,
          batch_count: 1,
        },
      };
      await route.fulfill({
        status: 200,
        contentType: "application/x-ndjson",
        body: `${JSON.stringify(progress)}\n${JSON.stringify({ type: "result", workspace })}\n`,
      });
    },
    { times: 1 }
  );
  await page.getByRole("button", { name: "Upload course", exact: true }).click();
  await expect(page.getByText("A copyright statement was found in the non-text content of the uploaded materials, and the course remains Private.").first()).toBeVisible();
  await expect(page.getByText("Upload information.pdf · page 2")).toBeVisible();
  await expect(page.getByText("All rights reserved.")).toBeVisible();

  await createPackageFromHome(page, packageTitle);
  const packagedLessonTitle = `课程包内课节 ${unique}`;
  await createLessonFromEmptyStudio(page, packagedLessonTitle);
  const packageContext = await page.evaluate(async ({ apiBase, expectedTitle, authToken }) => {
    const response = await fetch(`${apiBase}/api/workspace`, {
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    });
    const workspace = (await response.json()) as {
      packages: Array<{
        id: string;
        title: string;
        lessons: Array<{ id: string; history_graph: { commits: Array<{ id: string }> } }>;
      }>;
    };
    const coursePackage = workspace.packages.find((item) => item.title === expectedTitle);
    const packagedLesson = coursePackage?.lessons[0];
    if (!coursePackage || !packagedLesson) throw new Error("Public package lesson fixture was not found");
    return {
      packageId: coursePackage.id,
      lessonId: packagedLesson.id,
      historyNodeId: packagedLesson.history_graph.commits[0]?.id,
    };
  }, { apiBase: API_BASE_URL, expectedTitle: packageTitle, authToken: token });
  expect(packageContext.historyNodeId).toBeTruthy();

  await page.goto("/home");
  const packageCard = page.locator("[data-package-selection-root]").filter({ hasText: packageTitle }).first();
  await expect(packageCard).toBeVisible();
  await packageCard.click();
  const packageVisibilityResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/api/packages/${packageContext.packageId}`) && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Course package upload", exact: true }).click();
  expect((await packageVisibilityResponse).ok()).toBeTruthy();

  await page.goto(`/courses/shared/package/${packageContext.packageId}`);
  await expect(page.getByText(packageTitle).first()).toBeVisible();
  await expect(page.getByText("Public · Read only")).toBeVisible();

  await page.goto(
    `/courses/shared/lesson/${packageContext.lessonId}?history_node=${encodeURIComponent(packageContext.historyNodeId!)}`
  );
  await expect(page.getByText(packagedLessonTitle).first()).toBeVisible();
  await expect(page.getByText("Currently displayed are the historical nodes where the course is referenced.")).toBeVisible();
});

test("connects a personal API key from the Models panel without exposing it", async ({ page }) => {
  const unique = Date.now();
  const privateKey = "sk-browser-private-test";
  let personalApiConfigured = false;
  let submittedKey = "";
  const textModels = () => [
    {
      provider: "openai_codex",
      model: "gpt-5.5",
      access_method: "chatgpt_subscription",
      label: "OpenAI Codex test model",
      capability: "text",
      enabled: true,
      configured: true,
      default: true,
      supported_reasoning_efforts: [],
      service_tiers: [],
    },
    {
      provider: "deepseek",
      model: "deepseek-v4-flash",
      access_method: "platform_credits",
      label: "DeepSeek V4 Flash",
      capability: "text",
      enabled: true,
      configured: true,
      default: false,
      supported_reasoning_efforts: [],
      service_tiers: [],
    },
    {
      provider: "deepseek",
      model: "deepseek-v4-flash",
      access_method: "personal_api",
      label: "DeepSeek V4 Flash",
      capability: "text",
      enabled: personalApiConfigured,
      configured: personalApiConfigured,
      default: false,
      supported_reasoning_efforts: [],
      service_tiers: [],
    },
  ];

  await page.unroute("**/api/ai-models");
  await page.route("**/api/ai-models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: textModels(),
        realtime: [],
        defaults: {
          text: {
            provider: "openai_codex",
            model: "gpt-5.5",
            access_method: "chatgpt_subscription",
          },
          realtime: {
            provider: "openai",
            model: "gpt-realtime-2.1",
            access_method: "platform_credits",
          },
        },
      }),
    });
  });
  await page.route("**/api/model-credentials**", async (route) => {
    const method = route.request().method();
    if (method === "PUT") {
      submittedKey = (route.request().postDataJSON() as { api_key: string }).api_key;
      personalApiConfigured = true;
    } else if (method === "DELETE") {
      personalApiConfigured = false;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        method === "GET"
          ? [
              {
                provider: "deepseek",
                label: "DeepSeek",
                configured: personalApiConfigured,
                manageable: true,
              },
            ]
          : {
              provider: "deepseek",
              label: "DeepSeek",
              configured: personalApiConfigured,
              manageable: true,
            }
      ),
    });
  });
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "guest-model-credentials-e2e",
        email: "guest@openclass.local",
        role: "guest",
        created_at: "2026-07-25T00:00:00+00:00",
        auth_identities: [],
      }),
    });
  });

  await enterAsGuestThroughApi(page);
  await createPackageFromHome(page, `个人 API 测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `个人 API 测试页面 ${unique}`);
  await page.getByRole("button", { name: /Expand right (sidebar|column)/ }).click();
  await page.getByRole("button", { name: "Models" }).click();

  const keyInput = page.getByLabel("DeepSeek API Key");
  await expect(keyInput).toBeVisible();
  await keyInput.fill(privateKey);
  await page.getByRole("button", { name: "SaveKey" }).click();

  await expect(page.getByRole("status")).toHaveText("DeepSeek API Key Connected");
  await expect(keyInput).toHaveValue("");
  expect(submittedKey).toBe(privateKey);
  expect(
    await page.evaluate(() => JSON.stringify(window.localStorage))
  ).not.toContain(privateKey);

  const personalApiButton = page.getByRole("button", { name: /自有模型 API/ });
  await expect(personalApiButton).toBeEnabled();
  await personalApiButton.click();
  await page
    .getByRole("button", { name: /^DeepSeek V4 Flash DeepSeek/ })
    .click();
  await expect(page.getByText("Own model API · Shares the selection state with the chat input box")).toBeVisible();

  await page.getByRole("button", { name: "delete", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("DeepSeek API Key deleted");
  await expect(page.getByText("Not connected")).toBeVisible();
  await expect(page.getByText(privateKey)).toHaveCount(0);
});

test("creates an untitled lesson without asking for a name", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `无标题创建测试课程包 ${unique}`);
  await page.goto("/studio");

  const createResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/lessons/generate" &&
      response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Create first page" }).click();
  const response = await createResponse;

  expect(response.request().postDataJSON()).not.toHaveProperty("topic");
  await expect(page.getByRole("button", { name: "Untitled main" })).toBeVisible();
  await expect(page.getByLabel("First page name")).toHaveCount(0);
});

test("batch selects and deletes uploaded sources", async ({ page }) => {
  const unique = Date.now();
  const sourceRecords = [
    {
      id: `batch-source-a-${unique}`,
      title: `批量资料 A ${unique}`,
      file_name: `batch-a-${unique}.pdf`,
    },
    {
      id: `batch-source-b-${unique}`,
      title: `批量资料 B ${unique}`,
      file_name: `batch-b-${unique}.pdf`,
    },
  ].map((source, index) => ({
    ...source,
    owner_user_id: "guest-test",
    package_id: "package-test",
    source_type: "local_file",
    source_uri: null,
    mime_type: "application/pdf",
    size_bytes: 1024,
    status: "ready",
    error: "",
    open_notebook_notebook_id: "",
    open_notebook_source_id: "",
    open_notebook_command_id: "",
    structure_status: "linear_only",
    structure_strategy: "linear",
    structure_has_verified_toc: false,
    structure_error: "",
    structure_updated_at: new Date().toISOString(),
    ingestion_job: null,
    created_at: new Date(Date.UTC(2026, index, 1)).toISOString(),
    updated_at: new Date().toISOString(),
    metadata: {},
  }));
  let visibleSources = [...sourceRecords];
  const deletedSourceIds: string[] = [];
  const deleteRequestCounts = new Map<string, number>();
  let legacyStructureRebuildRequests = 0;
  let directoryCatalogRebuildRequests = 0;

  const legacyCatalog = (source: (typeof sourceRecords)[number]) => ({
    source: {
      id: source.id,
      title: source.title,
      file_name: source.file_name,
      mime_type: source.mime_type,
      size_bytes: source.size_bytes,
      status: source.status,
      structure_status: source.structure_status,
    },
    structure_id: null,
    status: source.structure_status,
    strategy: source.structure_strategy,
    has_verified_toc: false,
    catalog_version: 0,
    catalog_updated_at: source.structure_updated_at,
    source_content_hash: "",
    catalog_schema_version: "legacy",
    catalog_model: "",
    task_contract: "",
    work_state: "satisfied",
    phase: "terminal",
    directory_status: "complete",
    index_status: "complete",
    summary: "",
    next_plan: "",
    stop_reason: "",
    completion_reason: "",
    directory_gaps: [],
    pagination_regime_count: 0,
    unresolved_node_count: 0,
    locator_method: "",
    revision: 0,
    can_refine: false,
    recent_tool_activity: [],
    chapter_count: 0,
    verified_chapter_count: 0,
    confidence: 0,
    quality: null,
    error: "",
    warnings: [],
    chapters: [],
  });

  await page.route("**/api/packages/*/sources**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path.endsWith("/sources/catalogs")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          package_id: "package-test",
          catalogs: visibleSources.map(legacyCatalog),
        }),
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith("/catalog")) {
      const sourceId = path.split("/").at(-2) ?? "";
      const source = visibleSources.find((candidate) => candidate.id === sourceId);
      await route.fulfill({
        status: source ? 200 : 404,
        contentType: "application/json",
        body: JSON.stringify(source ? legacyCatalog(source) : { detail: "source not found" }),
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith("/sources")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(visibleSources),
      });
      return;
    }
    if (request.method() === "POST" && path.endsWith("/structure/rebuild")) {
      legacyStructureRebuildRequests += 1;
      const sourceId = path.split("/").at(-3) ?? "";
      const source = visibleSources.find((candidate) => candidate.id === sourceId);
      await route.fulfill({
        status: source ? 200 : 404,
        contentType: "application/json",
        body: JSON.stringify({ source, structure: null, chapters: [], chunks: [], visuals: [] }),
      });
      return;
    }
    if (request.method() === "POST" && path.endsWith("/catalog/rebuild")) {
      directoryCatalogRebuildRequests += 1;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "legacy source must not use directory catalog rebuild" }),
      });
      return;
    }
    if (request.method() === "DELETE") {
      const sourceId = path.split("/").at(-1) ?? "";
      const removedSource = visibleSources.find((source) => source.id === sourceId);
      deleteRequestCounts.set(sourceId, (deleteRequestCounts.get(sourceId) ?? 0) + 1);
      deletedSourceIds.push(sourceId);
      visibleSources = visibleSources.filter((source) => source.id !== sourceId);
      await new Promise((resolve) => setTimeout(resolve, 100));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(removedSource),
      });
      return;
    }
    await route.continue();
  });

  await enterAsGuest(page);
  await createPackageFromHome(page, `批量资料测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `批量资料测试页面 ${unique}`);
  await page.getByRole("button", { name: /Expand right (sidebar|column)/ }).click();
  await page.getByRole("button", { name: "Sources" }).click();

  await expect(page.getByText("GitHub repository", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connect to GitHub" })).toHaveCount(0);
  await expect(page.getByText("Uploaded 2 information")).toBeVisible();
  await expect(page.locator("[aria-label^=\"Rename source\"]").first()).toHaveAttribute(
    "aria-label",
    `Rename source 批量资料 B ${unique}`
  );
  await page.getByLabel("Data sorting").selectOption("name_asc");
  await expect(page.locator("[aria-label^=\"Rename source\"]").first()).toHaveAttribute(
    "aria-label",
    `Rename source 批量资料 A ${unique}`
  );
  await page.getByLabel("Data sorting").selectOption("uploaded_asc");
  await expect(page.locator("[aria-label^=\"Rename source\"]").first()).toHaveAttribute(
    "aria-label",
    `Rename source 批量资料 A ${unique}`
  );
  await page.getByLabel(`View directory status 批量资料 A ${unique}`).click();
  await page.getByLabel(`Re-create the data directory 批量资料 A ${unique}`).click();
  await expect.poll(() => legacyStructureRebuildRequests).toBe(1);
  expect(directoryCatalogRebuildRequests).toBe(0);

  await page.getByLabel(`Remove source 批量资料 A ${unique}`).dblclick();
  await expect.poll(() => deleteRequestCounts.get(sourceRecords[0].id)).toBe(1);
  await expect(page.getByLabel(`Remove source 批量资料 A ${unique}`)).toHaveCount(0);

  await page.getByRole("button", { name: "Batch management" }).click();
  await expect(page.getByLabel(`Select source 批量资料 B ${unique}`)).toBeVisible();
  await page.getByRole("button", { name: "Select all", exact: true }).click();
  await expect(page.getByText("Selected 1 / 1")).toBeVisible();

  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("Delete the 1 selected sources?");
    await dialog.accept();
  });
  await page.getByRole("button", { name: "Delete selected data in batches" }).click();

  await expect.poll(() => deletedSourceIds).toEqual(sourceRecords.map((source) => source.id));
  await expect(page.getByRole("button", { name: "Batch management" })).toHaveCount(0);
  await expect(page.getByText("Drag and drop files here, or click to upload data.")).toBeVisible();
});

test("backs off active source polling and stops once ingestion is ready", async ({ page }) => {
  const unique = Date.now();
  const requestTimes: number[] = [];
  const createdAt = new Date().toISOString();
  await page.route("**/api/packages/*/sources**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path.endsWith("/sources/catalogs")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ package_id: "package-test", catalogs: [] }),
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith("/sources")) {
      requestTimes.push(Date.now());
      const ready = requestTimes.length >= 3;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: `polling-source-${unique}`,
            owner_user_id: "guest-test",
            package_id: "package-test",
            title: `Polling source ${unique}`,
            source_type: "local_file",
            source_uri: null,
            file_name: `polling-${unique}.txt`,
            mime_type: "text/plain",
            size_bytes: 12,
            status: ready ? "ready" : "indexing",
            error: "",
            structure_status: ready ? "ready" : "pending",
            structure_strategy: ready ? "linear_text" : null,
            structure_has_verified_toc: false,
            structure_error: "",
            structure_updated_at: ready ? createdAt : null,
            ingestion_job: ready
              ? null
              : {
                  id: `job-${unique}`,
                  resource_id: null,
                  source_type: "local_file",
                  source_uri: null,
                  adapter: "native",
                  status: "indexing",
                  progress: 50,
                  error: "",
                  phase_history: ["indexing"],
                  agent_activity: [],
                  created_at: createdAt,
                  updated_at: createdAt,
                },
            created_at: createdAt,
            updated_at: createdAt,
            metadata: {},
          },
        ]),
      });
      return;
    }
    await route.continue();
  });

  await enterAsGuest(page);
  await createPackageFromHome(page, `Polling package ${unique}`);
  await createLessonFromEmptyStudio(page, `Polling lesson ${unique}`);
  await page.getByRole("button", { name: /Expand right (sidebar|column)/ }).click();
  await page.getByRole("button", { name: "Sources" }).click();

  await expect.poll(() => requestTimes.length, { timeout: 6000 }).toBe(3);
  expect(requestTimes[1] - requestTimes[0]).toBeGreaterThanOrEqual(800);
  expect(requestTimes[2] - requestTimes[1]).toBeGreaterThanOrEqual(1800);
  await page.waitForTimeout(1500);
  expect(requestTimes).toHaveLength(3);
});

test("does not poll a ready repository that has no document directory row", async ({ page }) => {
  const unique = Date.now();
  let sourceRequests = 0;
  const createdAt = new Date().toISOString();
  await page.route("**/api/packages/*/sources**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path.endsWith("/sources/catalogs")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ package_id: "package-test", catalogs: [] }),
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith("/sources")) {
      sourceRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: `repository-source-${unique}`,
            owner_user_id: "guest-test",
            package_id: "package-test",
            title: `Ready repository ${unique}`,
            source_type: "code_repository",
            source_uri: "https://github.com/example/repository",
            file_name: "repository.zip",
            mime_type: "application/zip",
            size_bytes: 12,
            status: "ready",
            error: "",
            structure_status: "pending",
            structure_strategy: null,
            structure_has_verified_toc: false,
            structure_error: "",
            structure_updated_at: null,
            ingestion_job: null,
            created_at: createdAt,
            updated_at: createdAt,
            metadata: {},
          },
        ]),
      });
      return;
    }
    await route.continue();
  });

  await enterAsGuest(page);
  await createPackageFromHome(page, `Repository polling package ${unique}`);
  await createLessonFromEmptyStudio(page, `Repository polling lesson ${unique}`);
  await page.getByRole("button", { name: /Expand right (sidebar|column)/ }).click();
  await page.getByRole("button", { name: "Sources" }).click();
  await expect.poll(() => sourceRequests).toBe(1);
  await page.waitForTimeout(2200);
  expect(sourceRequests).toBe(1);
});

test("lets file parsing use a supported faster service tier", async ({ page }) => {
  const unique = Date.now();
  const solModel = {
    provider: "openai_codex",
    model: "gpt-5.6-sol",
    label: "OpenAI Codex GPT-5.6-Sol",
    capability: "text",
    enabled: true,
    configured: true,
    default: true,
    default_reasoning_effort: "low",
    supported_reasoning_efforts: [{ reasoning_effort: "low", description: "" }],
    default_service_tier: null,
    service_tiers: [{ id: "priority", name: "Fast", description: "" }],
  };
  await page.unroute("**/api/ai-models");
  await page.route("**/api/ai-models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: [solModel],
        realtime: [],
        defaults: {
          text: {
            provider: solModel.provider,
            model: solModel.model,
            reasoning_effort: solModel.default_reasoning_effort,
            service_tier: null,
          },
          realtime: { provider: "openai_codex", model: "realtime-unavailable" },
        },
      }),
    });
  });

  await enterAsGuest(page);
  await createPackageFromHome(page, `Parsing speed package ${unique}`);
  await createLessonFromEmptyStudio(page, `Parsing speed lesson ${unique}`);
  await page.getByRole("button", { name: /Expand right (sidebar|column)/ }).click();
  await page.getByRole("button", { name: "Sources" }).click();

  const catalogModelButton = page.getByTestId("source-catalog-model-settings-button");
  await catalogModelButton.click();
  await page.getByTestId("source-catalog-model-speed-row").click();
  await page
    .getByTestId("source-catalog-model-speed-menu")
    .getByRole("button", { name: /speed fast/i })
    .click();

  await expect(catalogModelButton).toHaveAccessibleName(
    /Catalog Extraction Model Settings, current 5\.6 Sol, reasoning effort Mild, speed fast/i
  );
});

test("prefetches saved catalogs once and sends an authoritative chapter range", async ({ page }) => {
  const unique = Date.now();
  const solModel = {
    provider: "openai_codex",
    model: "gpt-5.6-sol",
    label: "OpenAI Codex GPT-5.6-Sol",
    capability: "text",
    enabled: true,
    configured: true,
    default: true,
    default_reasoning_effort: "low",
    supported_reasoning_efforts: [
      { reasoning_effort: "low", description: "" },
      { reasoning_effort: "high", description: "" },
    ],
    default_service_tier: null,
    service_tiers: [{ id: "priority", name: "Fast", description: "" }],
  };
  const lunaModel = {
    ...solModel,
    model: "gpt-5.6-luna",
    label: "OpenAI Codex GPT-5.6-Luna",
    default: false,
    default_reasoning_effort: "medium",
    supported_reasoning_efforts: [{ reasoning_effort: "medium", description: "" }],
    service_tiers: [],
  };
  const defaultOnlyModel = {
    ...lunaModel,
    model: "catalog-default-only",
    label: "OpenAI Codex Default-only test model",
    default_reasoning_effort: null,
    supported_reasoning_efforts: [],
  };
  const deepseekModel = {
    ...defaultOnlyModel,
    provider: "deepseek",
    model: "deepseek-v4-pro",
    label: "DeepSeek V4 Pro",
  };
  await page.unroute("**/api/ai-models");
  await page.route("**/api/ai-models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: [solModel, lunaModel, defaultOnlyModel, deepseekModel],
        realtime: [],
        defaults: {
          text: {
            provider: solModel.provider,
            model: solModel.model,
            reasoning_effort: solModel.default_reasoning_effort,
            service_tier: null,
          },
          realtime: { provider: "openai_codex", model: "realtime-unavailable" },
        },
      }),
    });
  });
  const sourceId = `catalog-source-${unique}`;
  const sourceTitle = `持久化目录资料 ${unique}`;
  const chapterTitle = `可引用章节 ${unique}`;
  const partialChapterTitle = `待验证章节 ${unique}`;
  const catalogUpdatedAt = new Date().toISOString();
  const initialContentHash = `hash-${unique}`;
  let advertisedContentHash = initialContentHash;
  let reportedStructureStatus = "building";
  let reportedStructureUpdatedAt = catalogUpdatedAt;
  const sourceRecord = {
    id: sourceId,
    owner_user_id: "guest-test",
    package_id: "package-test",
    title: sourceTitle,
    source_type: "local_file",
    source_uri: null,
    file_name: `catalog-${unique}.pdf`,
    mime_type: "application/pdf",
    size_bytes: 4096,
    status: "ready",
    error: "",
    structure_status: "ready",
    structure_strategy: "codex_directory_v1",
    structure_has_verified_toc: true,
    structure_quality: null,
    structure_error: "",
    structure_updated_at: catalogUpdatedAt,
    ingestion_job: null,
    created_at: catalogUpdatedAt,
    updated_at: catalogUpdatedAt,
    metadata: { content_hash: initialContentHash },
  };
  const verifiedChapter = {
    id: `chapter-verified-${unique}`,
    owner_user_id: "guest-test",
    package_id: "package-test",
    source_ingestion_id: sourceId,
    parent_id: null,
    number: "1",
    normalized_number: "1",
    title: chapterTitle,
    level: 1,
    path: [chapterTitle],
    order_index: 0,
    source_locator: "pdf:12-18",
    body_start_offset: null,
    body_end_offset: null,
    page_start: 12,
    page_end: 18,
    anchor_status: "verified",
    range: {
      kind: "pdf_pages",
      start: 12,
      end: 18,
      container: "",
      start_anchor: "",
      end_anchor: "",
      path: [chapterTitle],
      display_label: "pp. 12-18",
      end_inclusive: true,
      metadata: {},
    },
    mapping_status: "verified",
    source_content_hash: initialContentHash,
    catalog_evidence: [],
    catalog_version: 3,
    confidence: 0.98,
    excerpt: "",
    metadata: {},
  };
  const partialChapter = {
    ...verifiedChapter,
    id: `chapter-partial-${unique}`,
    parent_id: verifiedChapter.id,
    number: "1.1",
    normalized_number: "1.1",
    title: partialChapterTitle,
    level: 2,
    path: [chapterTitle, partialChapterTitle],
    order_index: 1,
    source_locator: "pdf:18",
    mapping_status: "partial",
  };
  const catalog = {
    source: {
      id: sourceId,
      title: sourceTitle,
      file_name: sourceRecord.file_name,
      mime_type: sourceRecord.mime_type,
      size_bytes: sourceRecord.size_bytes,
      status: "ready",
      structure_status: "ready",
    },
    structure_id: `structure-${unique}`,
    status: "ready",
    strategy: "codex_directory_v1",
    has_verified_toc: true,
    catalog_version: 3,
    catalog_updated_at: catalogUpdatedAt,
    source_content_hash: initialContentHash,
    catalog_schema_version: "codex_directory_v1",
    catalog_model: "openai_codex:test-model",
    task_contract: "",
    chapter_count: 2,
    verified_chapter_count: 1,
    confidence: 0.98,
    quality: null,
    error: "",
    warnings: [],
    chapters: [verifiedChapter, partialChapter],
  };
  let servedCatalog = catalog;
  let batchCatalogRequests = 0;
  let singleCatalogRequests = 0;
  let completedSingleCatalogResponses = 0;
  let rebuildRequests = 0;
  let staleSingleCatalogResponsesRemaining = 0;
  let delaySingleCatalogResponseAt = 0;
  let delayedSingleCatalogRequests = 0;
  let releaseDelayedSingleCatalog = () => {};
  let submittedSourceScope: Record<string, unknown> | null = null;
  let submittedSelection: Record<string, unknown> | null | undefined;
  let submittedSelections: Array<Record<string, unknown>> | undefined;
  let uploadPostData = "";
  let rebuildPostData = "";

  // Local product verification can reuse the already-running web build while keeping E2E writes in the isolated API database.
  await page.route("http://127.0.0.1:8000/api/**", async (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname === "/api/ai-models") {
      await route.fallback();
      return;
    }
    const headers = { ...request.headers() };
    delete headers.host;
    delete headers["content-length"];
    const response = await page.request.fetch(request.url().replace("127.0.0.1:8000", "127.0.0.1:8110"), {
      method: request.method(),
      headers,
      data: request.postDataBuffer() ?? undefined,
      failOnStatusCode: false,
    });
    await route.fulfill({ response });
  });

  await page.route("**/api/packages/*/sources**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path.endsWith("/sources/catalogs")) {
      batchCatalogRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ package_id: "package-test", catalogs: [servedCatalog] }),
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith(`/sources/${sourceId}/catalog`)) {
      singleCatalogRequests += 1;
      const responseCatalog = staleSingleCatalogResponsesRemaining > 0 ? catalog : servedCatalog;
      staleSingleCatalogResponsesRemaining = Math.max(0, staleSingleCatalogResponsesRemaining - 1);
      if (singleCatalogRequests === delaySingleCatalogResponseAt) {
        delayedSingleCatalogRequests += 1;
        await new Promise<void>((resolve) => {
          releaseDelayedSingleCatalog = resolve;
        });
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(responseCatalog) });
      completedSingleCatalogResponses += 1;
      return;
    }
    if (request.method() === "POST" && path.endsWith(`/sources/${sourceId}/catalog/rebuild`)) {
      rebuildRequests += 1;
      rebuildPostData = request.postData() ?? "";
      const rebuiltCatalog = {
        ...servedCatalog,
        catalog_version: 3 + rebuildRequests,
        catalog_updated_at: new Date(Date.now() + rebuildRequests * 1000).toISOString(),
        chapters: [
          {
            ...verifiedChapter,
            title: `${rebuildRequests === 1 ? "Chapter after reconstruction" : "Rebuild the chapter again"} ${unique}`,
            source_content_hash: advertisedContentHash,
            catalog_version: 3 + rebuildRequests,
          },
        ],
      };
      servedCatalog = rebuiltCatalog;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(rebuiltCatalog),
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith("/sources")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            ...sourceRecord,
            structure_status: reportedStructureStatus,
            structure_updated_at: reportedStructureUpdatedAt,
            metadata: { ...sourceRecord.metadata, content_hash: advertisedContentHash },
          },
        ]),
      });
      return;
    }
    if (request.method() === "POST" && path.endsWith("/sources")) {
      uploadPostData = request.postData() ?? "";
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sourceRecord) });
      return;
    }
    await route.continue();
  });
  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    const payload = route.request().postDataJSON() as {
      selection?: Record<string, unknown> | null;
      selections?: Array<Record<string, unknown>>;
      source_query_scope?: Record<string, unknown> | null;
    };
    submittedSelection = payload.selection;
    submittedSelections = payload.selections;
    submittedSourceScope = payload.source_query_scope ?? null;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "test stops after inspecting the chapter reference" }),
    });
  });

  await enterAsGuest(page);
  await createPackageFromHome(page, `目录缓存测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `目录缓存测试页面 ${unique}`);
  const viewport = page.viewportSize();
  if (!viewport) {
    throw new Error("Unable to read test viewport");
  }

  const chatModelButton = page.getByTestId("codex-model-settings-button");
  await chatModelButton.click();
  const chatModelMenu = page.getByTestId("codex-model-settings-menu");
  await expect(chatModelMenu).toBeVisible();
  await page.getByTestId("codex-model-model-row").click();
  const chatModelSubmenu = page.getByTestId("codex-model-model-menu");
  await expect(chatModelSubmenu).toBeVisible();
  const chatButtonBox = await chatModelButton.boundingBox();
  const chatMenuBox = await chatModelMenu.boundingBox();
  const chatSubmenuBox = await chatModelSubmenu.boundingBox();
  if (!chatButtonBox || !chatMenuBox || !chatSubmenuBox) {
    throw new Error("Chat model menu failed to complete viewport positioning");
  }
  expect(chatMenuBox.y + chatMenuBox.height).toBeLessThanOrEqual(chatButtonBox.y);
  expect(chatSubmenuBox.y + chatSubmenuBox.height).toBeLessThanOrEqual(chatButtonBox.y);
  expect(chatSubmenuBox.x + chatSubmenuBox.width).toBeLessThanOrEqual(viewport.width);
  await chatModelSubmenu.getByRole("button", { name: "Select model 5.6 Sol" }).click();
  await page.getByTestId("codex-model-reasoning-row").click();
  await page
    .getByTestId("codex-model-reasoning-menu")
    .getByRole("button", { name: /^(?:Reasoning effort high|High reasoning strength|推理强度 高)$/i })
    .click();
  await page.getByTestId("codex-model-speed-row").click();
  await page
    .getByTestId("codex-model-speed-menu")
    .getByRole("button", { name: "fast" })
    .click();
  await expect(chatModelButton).toHaveAccessibleName(
    /(?:Model settings, current 5\.6 Sol, reasoning effort high, speed fast|模型设置，当前 5\.6 Sol，推理强度 高，速度 快速)/i
  );
  await chatModelButton.click();
  await expect(chatModelMenu).toBeHidden();

  await page
    .getByRole("button", { name: /Expand right sidebar|Expand right column|展开右侧栏/ })
    .click();
  await page.getByRole("button", { name: "Sources" }).click();

  const catalogModelButton = page.getByTestId("source-catalog-model-settings-button");
  const catalogModelMenu = page.getByTestId("source-catalog-model-settings-menu");
  await expect(catalogModelButton).toHaveAccessibleName(
    /(?:Catalog Extraction Model Settings, current 5\.6 Sol, reasoning effort Mild, speed standard|目录提取模型设置，当前 5\.6 Sol，推理强度 轻度，速度 标准)/i
  );
  await catalogModelButton.click();
  await expect(catalogModelMenu).toBeVisible();
  const triggerBox = await catalogModelButton.boundingBox();
  const menuBox = await catalogModelMenu.boundingBox();
  if (!triggerBox || !menuBox) {
    throw new Error("Catalog model menu failed to complete viewport positioning");
  }
  expect(menuBox.y).toBeGreaterThanOrEqual(triggerBox.y + triggerBox.height);
  expect(menuBox.x).toBeGreaterThanOrEqual(0);
  expect(menuBox.x + menuBox.width).toBeLessThanOrEqual(viewport.width);
  expect(menuBox.y + menuBox.height).toBeLessThanOrEqual(viewport.height);

  await page.getByTestId("source-catalog-model-reasoning-row").click();
  const reasoningMenu = page.getByTestId("source-catalog-model-reasoning-menu");
  await expect(reasoningMenu).toBeVisible();
  const reasoningMenuBox = await reasoningMenu.boundingBox();
  if (!reasoningMenuBox) {
    throw new Error("Catalog model inference strength menu failed to complete viewport positioning");
  }
  expect(reasoningMenuBox.x + reasoningMenuBox.width).toBeLessThanOrEqual(menuBox.x);
  expect(reasoningMenuBox.x).toBeGreaterThanOrEqual(0);
  expect(reasoningMenuBox.y + reasoningMenuBox.height).toBeLessThanOrEqual(viewport.height);
  await reasoningMenu
    .getByRole("button", { name: /^(?:Reasoning effort high|High reasoning strength|推理强度 高)$/i })
    .click();
  await page.getByTestId("source-catalog-model-speed-row").click();
  await page
    .getByTestId("source-catalog-model-speed-menu")
    .getByRole("button", { name: /^(?:Speed fast|速度 快速)$/i })
    .click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /(?:Catalog Extraction Model Settings, current 5\.6 Sol, reasoning effort high, speed fast|目录提取模型设置，当前 5\.6 Sol，推理强度 高，速度 快速)/i
  );
  await catalogModelButton.click();
  await expect(catalogModelMenu).toBeHidden();
  await expect(catalogModelButton).toHaveAttribute("aria-expanded", "false");

  await expect.poll(() => batchCatalogRequests).toBe(1);
  await page.getByLabel(new RegExp(`^(?:View data directory|查看资料目录) ${sourceTitle}$`)).click();
  await expect(page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) })).toBeVisible();
  await page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) }).click();
  await expect(page.getByRole("button", { name: new RegExp(`^1\\.1 ${partialChapterTitle}`) })).toBeVisible();
  await expect(page.getByRole("button", { name: /引用章节到输入框|Reference chapter in composer/ })).toHaveCount(1);
  expect(singleCatalogRequests).toBe(0);

  reportedStructureUpdatedAt = new Date(Date.parse(catalogUpdatedAt) + 60_000).toISOString();
  delaySingleCatalogResponseAt = singleCatalogRequests + 1;
  await page.getByRole("button", { name: "History" }).click();
  await page.getByRole("button", { name: "Sources" }).click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /(?:Catalog Extraction Model Settings, current 5\.6 Sol, reasoning effort high, speed fast|目录提取模型设置，当前 5\.6 Sol，推理强度 高，速度 快速)/i
  );
  await expect.poll(() => delayedSingleCatalogRequests).toBe(1);
  await page.getByLabel(new RegExp(`^(?:View data directory|查看资料目录) ${sourceTitle}$`)).click();
  await expect(page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) })).toBeVisible();
  await expect(page.getByText("Reading directory…", { exact: true })).toHaveCount(0);
  reportedStructureUpdatedAt = catalogUpdatedAt;
  releaseDelayedSingleCatalog();
  await expect.poll(() => completedSingleCatalogResponses).toBe(2);
  expect(batchCatalogRequests).toBe(1);
  expect(singleCatalogRequests).toBe(2);

  await page
    .getByRole("button", {
      name: new RegExp(`^(?:Reference chapter in composer:|引用章节到输入框) 1 ${chapterTitle}$`),
    })
    .click();
  await expect(page.getByText(/^(?:资料问答范围|Data question and answer scope)$/)).toBeVisible();
  await expect(page.getByText(/^(?:引用 1 · 资料区段|Quote 1 · Information section)$/)).toHaveCount(0);
  await page.getByPlaceholder("Ask about the content of the selected data").fill("Please generate a blackboard based on this chapter");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect.poll(() => submittedSourceScope).not.toBeNull();
  await expect(page.getByText(/^(?:资料问答范围|Data question and answer scope)$/)).toHaveCount(0);
  expect(submittedSourceScope).toMatchObject({
    mode: "chapter",
    refs: [{
      source_ingestion_id: sourceId,
      source_chapter_id: verifiedChapter.id,
      source_content_hash: initialContentHash,
    }],
  });
  expect(submittedSelection).toBeNull();
  expect(submittedSelections).toEqual([]);

  const replacementContentHash = `replacement-hash-${unique}`;
  advertisedContentHash = replacementContentHash;
  reportedStructureStatus = "ready";
  staleSingleCatalogResponsesRemaining = 2;
  const replacementRequestBaseline = singleCatalogRequests;
  delaySingleCatalogResponseAt = replacementRequestBaseline + 2;
  servedCatalog = {
    ...catalog,
    source_content_hash: replacementContentHash,
    chapters: catalog.chapters.map((chapter) => ({
      ...chapter,
      source_content_hash: replacementContentHash,
    })),
  };
  await expect.poll(() => singleCatalogRequests, { timeout: 7_000 }).toBe(
    replacementRequestBaseline + 2
  );
  await expect.poll(() => delayedSingleCatalogRequests).toBe(2);

  await page
    .getByLabel(new RegExp(`^(?:Re-create the data directory|重新建立资料目录) ${sourceTitle}$`))
    .click();
  await expect.poll(() => rebuildRequests).toBe(1);
  expect(rebuildPostData).toContain('name="catalog_model"');
  expect(rebuildPostData).toContain('"provider":"openai_codex"');
  expect(rebuildPostData).toContain('"model":"gpt-5.6-sol"');
  expect(rebuildPostData).toContain('"reasoning_effort":"high"');
  expect(rebuildPostData).toContain('"service_tier":"priority"');
  releaseDelayedSingleCatalog();
  await expect.poll(() => completedSingleCatalogResponses).toBe(
    replacementRequestBaseline + 2
  );
  await expect(
    page.getByRole("button", {
      name: new RegExp(`^1 (?:Chapter after reconstruction|重建后章节) ${unique}`),
    })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) })).toHaveCount(0);

  await catalogModelButton.click();
  await page.getByTestId("source-catalog-model-model-row").click();
  await page
    .getByTestId("source-catalog-model-model-menu")
    .getByRole("button", { name: "Select model 5.6 Luna" })
    .click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /(?:Catalog Extraction Model Settings, current 5\.6 Luna, reasoning effort (?:medium|middle), speed standard|目录提取模型设置，当前 5\.6 Luna，推理强度 中，速度 标准)/i
  );
  await expect(page.getByTestId("source-catalog-model-reasoning-row")).toHaveCount(0);
  await expect(page.getByTestId("source-catalog-model-speed-row")).toHaveCount(0);
  await catalogModelButton.click();

  await page.getByTestId("source-file-input").setInputFiles({
    name: `catalog-model-${unique}.pdf`,
    mimeType: "application/pdf",
    buffer: Buffer.from("catalog model upload"),
  });
  await expect.poll(() => uploadPostData).toContain('name="catalog_model"');
  expect(uploadPostData).toContain('"provider":"openai_codex"');
  expect(uploadPostData).toContain('"model":"gpt-5.6-luna"');
  expect(uploadPostData).toContain('"reasoning_effort":"medium"');
  expect(uploadPostData).toContain('"service_tier":null');

  await catalogModelButton.click();
  await page.getByTestId("source-catalog-model-model-row").click();
  await page
    .getByTestId("source-catalog-model-model-menu")
    .getByRole("button", { name: "Select modelDeepSeek V4 Pro" })
    .click();
  await page.getByTestId("source-file-input").setInputFiles({
    name: `catalog-provider-${unique}.pdf`,
    mimeType: "application/pdf",
    buffer: Buffer.from("catalog provider upload"),
  });
  await expect.poll(() => uploadPostData).toContain('"provider":"deepseek"');
  expect(uploadPostData).toContain('"model":"deepseek-v4-pro"');

  await catalogModelButton.click();
  await page.getByTestId("source-catalog-model-model-row").click();
  await page
    .getByTestId("source-catalog-model-model-menu")
    .getByRole("button", { name: "Select modelDefault only test model" })
    .click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 Default only test model，推理强度 默认，速度 标准/
  );
  await expect(page.getByTestId("source-catalog-model-reasoning-row")).toHaveCount(0);
  await expect(page.getByTestId("source-catalog-model-speed-row")).toHaveCount(0);
  await page.getByTestId("source-catalog-model-reset-button").click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 5\.6 Sol，推理强度 轻度，速度 标准/
  );
  await catalogModelButton.click();
  await expect(catalogModelMenu).toBeHidden();
  await page.getByRole("button", { name: "History" }).click();
  await page.getByRole("button", { name: "Sources" }).click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 5\.6 Sol，推理强度 轻度，速度 标准/
  );
});

test("restores each lesson's attached composer reference after switching tabs", async ({ page }) => {
  const unique = Date.now();
  const firstTitle = `引用保留页面一 ${unique}`;
  const secondTitle = `引用保留页面二 ${unique}`;
  const referencedText = `需要保留的引用内容 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `引用保留课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, firstTitle);
  await writeEditorTextAndWaitForSave(page, referencedText);

  await createLessonFromTabBar(page, secondTitle);

  await page.getByRole("button", { name: `${firstTitle} main` }).click();
  const editor = page.locator(".ProseMirror").first();
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.getByRole("button", { name: "Reference to input box" }).click();
  await expect(page.getByLabel("Remove reference")).toBeVisible();
  await expect(page.getByText(referencedText, { exact: false }).last()).toBeVisible();
  await page.getByPlaceholder("Continue to ask questions based on the selected content").click();
  await expect(page.getByLabel("Remove reference")).toBeVisible();

  await page.getByRole("button", { name: `${secondTitle} main` }).click();
  await expect(page.getByLabel("Remove reference")).toHaveCount(0);

  await page.getByRole("button", { name: `${firstTitle} main` }).click();
  await expect(page.getByLabel("Remove reference")).toBeVisible();
  await expect(page.getByText(referencedText, { exact: false }).last()).toBeVisible();
});

test("keeps an async chat result and editor draft isolated to the lesson that started the turn", async ({ page }) => {
  const unique = Date.now();
  const firstTitle = `异步隔离页面一 ${unique}`;
  const secondTitle = `异步隔离页面二 ${unique}`;
  const firstInitialText = `页面一原始内容 ${unique}`;
  const secondText = `页面二必须保留的内容 ${unique}`;
  const secondComposerDraft = `页面二尚未发送的问题 ${unique}`;
  const submittedMessage = `只更新页面一 ${unique}`;
  const generatedText = `页面一异步生成结果 ${unique}`;
  const assistantMessage = `页面一独立回复 ${unique}`;
  let releaseChatResponse!: () => void;
  let chatResponsePrepared!: () => void;
  const chatResponseGate = new Promise<void>((resolve) => {
    releaseChatResponse = resolve;
  });
  const chatPrepared = new Promise<void>((resolve) => {
    chatResponsePrepared = resolve;
  });

  await enterAsGuest(page);
  await createPackageFromHome(page, `异步隔离课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, firstTitle);
  await writeEditorTextAndWaitForSave(page, firstInitialText);
  await createLessonFromTabBar(page, secondTitle);
  await writeEditorTextAndWaitForSave(page, secondText);
  await page.getByRole("button", { name: `${firstTitle} main` }).click();

  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const responsePackage = await currentPackageResponse.json();
    const requestLessonId = new URL(route.request().url()).pathname.split("/").at(-3);
    const requestLesson = responsePackage.lessons.find(
      (lesson: { id: string }) => lesson.id === requestLessonId
    );
    const otherLesson = responsePackage.lessons.find(
      (lesson: { id: string }) => lesson.id !== requestLessonId
    );
    const generatedDocument = {
      ...requestLesson.board_document,
      content_json: {
        type: "doc",
        content: [{ type: "paragraph", content: [{ type: "text", text: generatedText }] }],
      },
      content_html: `<p>${generatedText}</p>`,
      content_text: generatedText,
    };
    requestLesson.board_document = generatedDocument;
    responsePackage.active_lesson_id = requestLesson.id;
    otherLesson.board_document = {
      ...otherLesson.board_document,
      content_json: {
        type: "doc",
        content: [{ type: "paragraph", content: [{ type: "text", text: "stale other lesson" }] }],
      },
      content_html: "<p>stale other lesson</p>",
      content_text: "stale other lesson",
    };
    const branch = requestLesson.history_graph.branches[requestLesson.history_graph.current_branch];
    const commitId = `commit_async_lesson_isolation_${unique}`;
    requestLesson.history_graph.commits.push({
      id: commitId,
      label: "Chat flow",
      message: "Persisted isolated async chat result",
      branch_name: requestLesson.history_graph.current_branch,
      created_at: new Date().toISOString(),
      parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
      operations: [],
      snapshot: generatedDocument,
      metadata: {
        kind: "chat_flow",
        user_message: submittedMessage,
        assistant_message: assistantMessage,
      },
    });
    branch.head_commit_id = commitId;
    chatResponsePrepared();
    await chatResponseGate;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: final\ndata: ${JSON.stringify({
        chatbot_message: assistantMessage,
        agent_activity: [],
        learning_requirement_sheet: null,
        active_requirement_sheet: null,
        learning_clarification: null,
        requirement_run_id: null,
        requirement_version_id: null,
        requirement_phase: null,
        learning_requirement_operation_status: "none",
        learning_requirement_operation_failure_reason: null,
        board_task_sheet: null,
        active_board_task_sheet: null,
        board_task_run_id: null,
        board_task_version_id: null,
        board_task_phase: null,
        board_task_questions: [],
        board_decision: { action: "replace", reason: "isolated test update" },
        needs_clarification: false,
        clarification_questions: [],
        requirement_cleared: false,
        board_document_operation_status: "succeeded",
        board_document_operation_failure_reason: null,
        teaching_progress: null,
        course_package: responsePackage,
      })}\n\n`,
    });
  });

  await page.getByPlaceholder("Send a message to OpenClass...").fill(submittedMessage);
  await page.getByRole("button", { name: "Send message" }).click();
  await chatPrepared;

  await page.getByRole("button", { name: `${secondTitle} main` }).click();
  await expect(page.locator(".ProseMirror").first()).toContainText(secondText);
  await page.getByPlaceholder("Send a message to OpenClass...").fill(secondComposerDraft);
  releaseChatResponse();

  await expect(page.getByPlaceholder("Send a message to OpenClass...")).toHaveValue(secondComposerDraft);
  await expect(page.locator(".ProseMirror").first()).toContainText(secondText);
  await expect(page.getByText(assistantMessage)).toHaveCount(0);

  await page.getByRole("button", { name: `${firstTitle} main` }).click();
  await expect(page.locator(".ProseMirror").first()).toContainText(generatedText);
  await expect(page.getByRole("complementary").getByText(assistantMessage).first()).toBeVisible();

  await page.getByRole("button", { name: `${secondTitle} main` }).click();
  await expect(page.locator(".ProseMirror").first()).toContainText(secondText);
  await expect(page.getByPlaceholder("Send a message to OpenClass...")).toHaveValue(secondComposerDraft);
});

test("references board content into the geometry workspace and renders a generated scene", async ({ page }) => {
  const unique = Date.now();
  const referencedText = `在四边形 ABCD 中，AB 平行于 CD，连接 AC 与 BD ${unique}`;
  const sourceId = `source_geometry_attachment_${unique}`;
  const fileName = `geometry-question-${unique}.png`;
  let generationPayload: Record<string, unknown> | null = null;

  await enterAsGuest(page);
  await createPackageFromHome(page, `图形生成课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `图形生成页面 ${unique}`);
  await writeEditorTextAndWaitForSave(page, referencedText);

  await page.route("**/api/packages/*/sources", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: sourceId,
        owner_user_id: "guest-test",
        package_id: "package-test",
        title: fileName,
        source_type: "local_file",
        source_uri: null,
        file_name: fileName,
        mime_type: "image/png",
        size_bytes: 68,
        status: "queued",
        error: "",
        open_notebook_notebook_id: "",
        open_notebook_source_id: "",
        open_notebook_command_id: "",
        structure_status: "pending",
        structure_strategy: null,
        structure_has_verified_toc: false,
        structure_error: "",
        structure_updated_at: null,
        ingestion_job: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        metadata: {},
      }),
    });
  });

  await page.route("**/api/lessons/*/geometry/generate", async (route) => {
    generationPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        version: "1.0",
        title: "parallel sided quadrilateral",
        summary: "Use a set of representative coordinates to present the parallel relationships in the question.",
        dimension: "3d",
        show_axes: true,
        show_grid: true,
        viewport: { x_min: -4, x_max: 4, y_min: -3, y_max: 3 },
        points: [
          { id: "A", label: "A", x: -2, y: 1, z: 0, color: "#38bdf8", hidden: false },
          { id: "B", label: "B", x: 2, y: 1, z: 0, color: "#38bdf8", hidden: false },
          { id: "C", label: "C", x: 1.5, y: -1, z: 1, color: "#f59e0b", hidden: false },
          { id: "D", label: "D", x: -1.5, y: -1, z: 1, color: "#f59e0b", hidden: false },
        ],
        primitives: [
          { id: "AB", kind: "segment", label: "AB", point_ids: ["A", "B"], center_id: "", radius: null, radius_y: null, text: "", color: "#38bdf8", fill: "none", opacity: 1, stroke_width: 3, dashed: false },
          { id: "CD", kind: "segment", label: "CD", point_ids: ["D", "C"], center_id: "", radius: null, radius_y: null, text: "", color: "#f59e0b", fill: "none", opacity: 1, stroke_width: 3, dashed: false },
          { id: "ABCD", kind: "polygon", label: "ABCD", point_ids: ["A", "B", "C", "D"], center_id: "", radius: null, radius_y: null, text: "", color: "#94a3b8", fill: "rgba(56,189,248,0.12)", opacity: 1, stroke_width: 1.5, dashed: false },
        ],
        steps: ["AB and CD are represented by line segments in the same direction."],
        source_excerpt: referencedText,
      }),
    });
  });

  const editor = page.locator(".ProseMirror").first();
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.getByRole("button", { name: "Reference to graphics" }).click();

  const geometryPanel = page.locator("[data-geometry-generation-panel]");
  await expect(page.getByRole("button", { name: "Geometry" })).toBeVisible();
  await expect(geometryPanel.getByText("Geometry generation")).toBeVisible();
  await expect(geometryPanel.getByText(referencedText, { exact: true })).toBeVisible();
  await expect(geometryPanel.getByText("Add photos and files")).toBeVisible();
  await geometryPanel.getByRole("button", { name: "Add attachment" }).click();
  await expect(page.getByRole("menuitem", { name: "add picture" })).toBeVisible();
  await page.getByTestId("geometry-image-input").setInputFiles({
    name: fileName,
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64"
    ),
  });
  await expect(geometryPanel.getByLabel("Attachment added")).toContainText(fileName);
  await geometryPanel.getByRole("button", { name: "Generate graphics" }).click();

  await expect(page.getByRole("img", { name: "Parallel-sided quadrilateral interactive graphics" })).toBeVisible();
  await expect(page.getByText("3D · Drag to rotate")).toBeVisible();
  const submittedPayload = generationPayload as Record<string, unknown> | null;
  expect((submittedPayload?.["selection"] as { excerpt?: string } | undefined)?.excerpt).toBe(referencedText);
  expect(submittedPayload?.["attachments"]).toEqual([
    expect.objectContaining({
      source_ingestion_id: sourceId,
      name: fileName,
      mime_type: "image/png",
      kind: "image",
    }),
  ]);
});

test("adds images and files from the chat plus menu and includes them in the turn", async ({ page }) => {
  const unique = Date.now();
  const sourceId = `source_chat_attachment_${unique}`;
  const fileName = `diagram-${unique}.png`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `聊天附件测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `聊天附件测试页面 ${unique}`);

  await page.route("**/api/packages/*/sources", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: sourceId,
        owner_user_id: "guest-test",
        package_id: "package-test",
        title: fileName,
        source_type: "local_file",
        source_uri: null,
        file_name: fileName,
        mime_type: "image/png",
        size_bytes: 68,
        status: "queued",
        error: "",
        open_notebook_notebook_id: "",
        open_notebook_source_id: "",
        open_notebook_command_id: "",
        structure_status: "pending",
        structure_strategy: null,
        structure_has_verified_toc: false,
        structure_error: "",
        structure_updated_at: null,
        ingestion_job: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        metadata: {},
      }),
    });
  });
  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "test stops after inspecting the request" }),
    });
  });

  await page.getByRole("button", { name: "Add attachment" }).click();
  const attachmentButtonBox = await page.getByRole("button", { name: "Add attachment" }).boundingBox();
  const textModelPickerBox = await page.getByTestId("codex-model-settings-button").boundingBox();
  const attachmentMenuBox = await page.getByRole("menu", { name: "Add content" }).boundingBox();
  expect(attachmentButtonBox).not.toBeNull();
  expect(textModelPickerBox).not.toBeNull();
  expect(attachmentMenuBox).not.toBeNull();
  expect(attachmentButtonBox?.y ?? 0).toBeGreaterThanOrEqual(
    (textModelPickerBox?.y ?? 0) + (textModelPickerBox?.height ?? 0)
  );
  expect((attachmentMenuBox?.y ?? 0) + (attachmentMenuBox?.height ?? 0)).toBeLessThanOrEqual(
    textModelPickerBox?.y ?? 0
  );
  await expect(page.getByRole("menuitem", { name: "add picture" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Add files" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Expand tablet" })).toBeVisible();
  await page.getByTestId("chat-image-input").setInputFiles({
    name: fileName,
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64"
    ),
  });

  await expect(page.getByLabel("Attachment added")).toContainText(fileName);
  await expect(page.getByRole("button", { name: `移除附件 ${fileName}` })).toBeVisible();
  await page.getByPlaceholder("Send a message to OpenClass...").fill("Please answer based on this picture");
  const chatRequestPromise = page.waitForRequest(
    (request) => request.url().includes("/chat/stream") && request.method() === "POST"
  );
  await page.getByRole("button", { name: "Send message" }).click();
  const chatRequest = await chatRequestPromise;
  const payload = chatRequest.postDataJSON() as { attachments?: Array<Record<string, unknown>> };
  expect(payload.attachments).toHaveLength(1);
  expect(payload.attachments?.[0]).toMatchObject({
    source_ingestion_id: sourceId,
    name: fileName,
    mime_type: "image/png",
    size_bytes: 68,
    kind: "image",
  });
});

test("adds a handwriting board image from the chat plus menu", async ({ page }) => {
  const unique = Date.now();
  const sourceId = `source_chat_ink_${unique}`;
  const fileName = `handwriting-${unique}.png`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `聊天手写板测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `聊天手写板测试页面 ${unique}`);

  await page.route("**/api/packages/*/sources", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: sourceId,
        owner_user_id: "guest-test",
        package_id: "package-test",
        title: fileName,
        source_type: "local_file",
        source_uri: null,
        file_name: fileName,
        mime_type: "image/png",
        size_bytes: 256,
        status: "queued",
        error: "",
        open_notebook_notebook_id: "",
        open_notebook_source_id: "",
        open_notebook_command_id: "",
        structure_status: "pending",
        structure_strategy: null,
        structure_has_verified_toc: false,
        structure_error: "",
        structure_updated_at: null,
        ingestion_job: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        metadata: {},
      }),
    });
  });

  await page.getByRole("button", { name: "Add attachment" }).click();
  await page.getByRole("menuitem", { name: "Expand tablet" }).click();
  await expect(page.getByRole("dialog", { name: "writing tablet" })).toBeVisible();

  const addButton = page.getByRole("button", { name: "add to message" });
  await expect(addButton).toBeDisabled();
  const canvas = page.getByLabel("Handwriting input drawing board");
  const canvasBox = await canvas.boundingBox();
  expect(canvasBox).not.toBeNull();
  if (!canvasBox) {
    return;
  }
  await page.mouse.move(canvasBox.x + 30, canvasBox.y + 30);
  await page.mouse.down();
  await page.mouse.move(canvasBox.x + 120, canvasBox.y + 90, { steps: 5 });
  await page.mouse.up();
  await expect(addButton).toBeEnabled();
  await addButton.click();

  await expect(page.getByRole("dialog", { name: "writing tablet" })).toBeHidden();
  await expect(page.getByLabel("Attachment added")).toContainText(fileName);
});

test("places the create control first and orders lesson tabs from newest to oldest", async ({ page }) => {
  const unique = Date.now();
  const firstTitle = `较早课程 ${unique}`;
  const secondTitle = `最近课程 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `课程顺序测试包 ${unique}`);
  await createLessonFromEmptyStudio(page, firstTitle);

  await createLessonFromTabBar(page, secondTitle);

  const lessonTabList = page
    .getByRole("navigation")
    .filter({ has: page.getByRole("button", { name: `${secondTitle} main` }) });
  const lessonTabs = lessonTabList.locator(":scope > button");
  await expect(lessonTabs.nth(0)).toHaveAccessibleName("Create new page");
  await expect(lessonTabs.nth(1)).toHaveAccessibleName(`${secondTitle} main`);
  await expect(lessonTabs.nth(2)).toHaveAccessibleName(`${firstTitle} main`);
});

test("uses the top-right profile avatar as the only account menu in Studio", async ({ page }) => {
  await enterAsGuest(page);

  const accountMenu = page.locator("[data-account-menu-root]");
  await expect(accountMenu).toHaveCount(1);
  await page.getByRole("button", { name: "Open Classroom User Avatar" }).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Sign in to save" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "End visitor visit" })).toBeVisible();
});

test("manages standalone lessons from the profile project list", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `个人项目管理课程 ${unique}`;
  const renamedLessonTitle = `${lessonTitle} 已重命名`;
  const targetPackageTitle = `个人项目目标课程包 ${unique}`;
  const token = await enterAsMemberThroughApi(page);
  const authorization = { Authorization: `Bearer ${token}` };
  const generated = await page.request.post(`${API_BASE_URL}/api/lessons/generate`, {
    headers: authorization,
    data: { topic: lessonTitle, start_blank: true },
  });
  expect(generated.ok()).toBeTruthy();
  const createdPackage = await page.request.post(`${API_BASE_URL}/api/packages`, {
    headers: authorization,
    data: { title: targetPackageTitle, summary: "" },
  });
  expect(createdPackage.ok()).toBeTruthy();

  await page.goto("/profile?tab=repositories");
  const manageLessonButton = page.getByRole("button", { name: `管理课程 ${lessonTitle}` });
  await expect(manageLessonButton).toBeVisible();
  await manageLessonButton.click();

  const lessonMenu = page.locator(`div[aria-label="管理课程 ${lessonTitle}"]`);
  await expect(lessonMenu).toBeVisible();
  await expect(lessonMenu.getByRole("button", { name: "Course is set to Private", exact: true })).toHaveCount(0);
  await expect(lessonMenu.getByRole("button", { name: "Course is set to Public", exact: true })).toHaveCount(0);
  await lessonMenu.getByRole("button", { name: "Course upload course", exact: true }).click();
  await expect(lessonMenu.getByText("The course has no uploaded materials and can be made public.")).toBeVisible();
  await expect(lessonMenu.getByText("The current public version remains unchanged; it will not be updated until it is uploaded again.")).toBeVisible();
  await expect(lessonMenu.getByRole("button", { name: "share", exact: true })).toBeEnabled();
  await expect(lessonMenu.getByRole("button", { name: "Rename", exact: true })).toBeVisible();
  await expect(lessonMenu.getByRole("button", { name: "Export course package", exact: true })).toBeVisible();

  await lessonMenu.getByRole("button", { name: "Move to course package", exact: true }).click();
  await expect(lessonMenu.getByRole("button", { name: targetPackageTitle, exact: true })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept(renamedLessonTitle));
  await lessonMenu.getByRole("button", { name: "Rename", exact: true }).click();
  await expect(page.getByRole("button", { name: `管理课程 ${renamedLessonTitle}` })).toBeVisible();

  const renamedManageLessonButton = page.getByRole("button", { name: `管理课程 ${renamedLessonTitle}` });
  await renamedManageLessonButton.click();
  const renamedLessonMenu = page.locator(`div[aria-label="管理课程 ${renamedLessonTitle}"]`);
  const downloadPromise = page.waitForEvent("download");
  await renamedLessonMenu.getByRole("button", { name: "Export course package", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.ridoc$/);

  await renamedManageLessonButton.click();
  await renamedLessonMenu.getByRole("button", { name: "Move to course package", exact: true }).click();
  await renamedLessonMenu.getByRole("button", { name: targetPackageTitle, exact: true }).click();
  await expect(renamedManageLessonButton).toBeHidden();

  await page.getByRole("button", { name: `管理课程包 ${targetPackageTitle}` }).click();
  const packageMenu = page.locator(`div[aria-label="管理课程包 ${targetPackageTitle}"]`);
  await expect(packageMenu).toBeVisible();
  await expect(packageMenu.getByRole("button", { name: "Course package set to Private", exact: true })).toHaveCount(0);
  await expect(packageMenu.getByRole("button", { name: "Course package is set to Public", exact: true })).toHaveCount(0);
  await expect(packageMenu.getByRole("button", { name: "Course package upload course", exact: true })).toBeVisible();
  await expect(packageMenu.getByRole("button", { name: "share", exact: true })).toBeDisabled();
  await expect(packageMenu.getByRole("button", { name: "Rename", exact: true })).toBeVisible();
});

test("sends a contact message from the home page without opening a mail client", async ({ page }) => {
  const guestResponse = await page.request.post(`${API_BASE_URL}/api/auth/guest`);
  expect(guestResponse.ok()).toBeTruthy();
  const { token } = (await guestResponse.json()) as { token: string };
  await page.context().addCookies([
    { name: "openclass.auth.token", value: token, domain: "127.0.0.1", path: "/" },
  ]);
  await page.addInitScript((authToken) => {
    window.localStorage.setItem("openclass.auth.token", authToken);
  }, token);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "contact-test-user",
        email: "learner@example.com",
        role: "user",
        display_name: "Learner",
        avatar_url: null,
        created_at: "2026-07-29T00:00:00Z",
        last_login_at: null,
        email_verified_at: "2026-07-29T00:00:00Z",
        auth_identities: [],
      }),
    }),
  );
  let submitted: { subject?: string; message?: string } = {};
  await page.route("**/api/contact", async (route) => {
    submitted = route.request().postDataJSON() as { subject: string; message: string };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ message: "Contact message sent" }),
    });
  });
  await page.goto("/");

  const contactButton = page.getByRole("button", { name: "To contact OpenClass, send a message to hello@open-classes.com" });
  await expect(contactButton).toBeVisible();
  await contactButton.click();
  await expect(page.getByRole("dialog", { name: "Contact the Open Classroom team" })).toBeVisible();
  await page.getByLabel("theme").fill("product suggestions");
  await page.getByLabel("Contact content").fill("Please contact the Open Classroom team directly through the form on the site.");
  await page.getByRole("button", { name: "Send message", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Message sent" })).toBeVisible();
  expect(submitted).toEqual({
    subject: "product suggestions",
    message: "Please contact the Open Classroom team directly through the form on the site.",
  });
});

test("collapses course package and standalone lesson lists independently", async ({ page }) => {
  await enterAsMemberThroughApi(page);

  const packageList = page.locator("#learning-home-course-packages");
  const standaloneList = page.locator("#learning-home-standalone-lessons");
  const collapsePackages = page.getByLabel("Collapse course package");
  const collapseStandaloneLessons = page.getByLabel("Close individual courses");

  await expect(packageList).toBeVisible();
  await expect(packageList).toHaveCSS("overflow-y", "auto");
  await expect(standaloneList).toBeVisible();

  await collapsePackages.click();
  await expect(packageList).toBeHidden();
  await expect(page.getByLabel("Expand course package")).toHaveAttribute("aria-expanded", "false");
  await expect(standaloneList).toBeVisible();

  await collapseStandaloneLessons.click();
  await expect(standaloneList).toBeHidden();
  await expect(page.getByLabel("Expand individual courses")).toHaveAttribute("aria-expanded", "false");
});

test("exports and imports a RIDOC file as a standalone lesson", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `主页课程包入口 ${unique}`;
  await enterAsMemberThroughApi(page);
  await page.getByLabel("Add individual courses").click();
  await expect(page.getByRole("menuitem", { name: "Import course files" })).toBeVisible();
  await nameNextGeneratedLessonForTest(page, lessonTitle);
  await page.getByRole("menuitem", { name: "Create new course" }).click();
  await expect(page.getByRole("button", { name: `${lessonTitle} main` })).toBeVisible();
  await expect(page.locator(".ProseMirror")).toBeVisible();
  await writeEditorTextAndWaitForSave(page, `主页导出内容 ${unique}`);
  await page.goto("/home");

  const lessonCard = page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle });
  await lessonCard.getByLabel("Open the course operation menu").click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export course package", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.ridoc$/);
  const ridocStream = await download.createReadStream();
  const ridocChunks: Buffer[] = [];
  for await (const chunk of ridocStream) {
    ridocChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  await page.getByLabel("Add individual courses").click();
  const importResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/workspace/import-ridoc") && response.request().method() === "POST"
  );
  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("menuitem", { name: "Import course files", exact: true }).click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: download.suggestedFilename(),
    mimeType: "application/vnd.openclass.ridoc+zip",
    buffer: Buffer.concat(ridocChunks),
  });
  await importResponse;

  await expect(page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle })).toHaveCount(2);
});

test("renames a standalone lesson from its actions menu", async ({ page }) => {
  const unique = Date.now();
  const originalTitle = `待重命名课程 ${unique}`;
  const renamedTitle = `已重命名课程 ${unique}`;

  await enterAsMemberThroughApi(page);
  await page.getByLabel("Add individual courses").click();
  await nameNextGeneratedLessonForTest(page, originalTitle);
  await page.getByRole("menuitem", { name: "Create new course" }).click();
  await expect(page.getByRole("button", { name: `${originalTitle} main` })).toBeVisible();
  await page.goto("/home");

  const lessonCard = page.locator("[data-lesson-selection-root]").filter({ hasText: originalTitle });
  await lessonCard.getByLabel("Open the course operation menu").click();
  await expect(page.getByRole("button", { name: "Rename", exact: true })).toBeVisible();
  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("prompt");
    expect(dialog.defaultValue()).toBe(originalTitle);
    await dialog.accept(`  ${renamedTitle}  `);
  });
  const renameResponse = page.waitForResponse(
    (response) =>
      /\/api\/lessons\/[^/]+\/rename$/.test(new URL(response.url()).pathname) &&
      response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await renameResponse;

  await expect(page.locator("[data-lesson-selection-root]").filter({ hasText: renamedTitle })).toBeVisible();
  await expect(page.locator("[data-lesson-selection-root]").filter({ hasText: originalTitle })).toHaveCount(0);
});

test("localizes the empty course package page in English", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `English empty package ${unique}`);
  await page.goto("/studio");

  await expect(page.getByText("This package is empty")).toBeVisible();
  await setInterfaceLanguage(page, "en");
  await expect(page.getByRole("heading", { name: "This package is empty" })).toBeVisible();
  await expect(page.getByText("Create the page and start chatting with AI.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Create first page" }).click();
  await expect(page.getByRole("button", { name: "Untitled main" })).toBeVisible();
  await expect(page.getByLabel("First page name")).toHaveCount(0);
});

test("restores an older document version from history", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `恢复测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `恢复测试页面 ${unique}`);

  const firstVersion = `历史版本一 ${unique}`;
  const secondVersion = `历史版本二 ${unique}`;
  await writeEditorTextAndWaitForSave(page, firstVersion);
  await writeEditorTextAndWaitForSave(page, secondVersion);
  await openHistoryPanel(page);

  const restoreResponse = page.waitForResponse(
    (response) => response.url().includes("/restore") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Restore" }).nth(1).click();
  await restoreResponse;

  const editor = page.locator(".ProseMirror").first();
  await expect(editor).toContainText(firstVersion);
  await expect(editor).not.toContainText(secondVersion);
});

test("merges a lesson branch through a persistent editable draft", async ({ page }) => {
  const unique = Date.now();
  const sourceBranch = `source-${unique}`;
  await enterAsGuest(page);
  await createPackageFromHome(page, `合并测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `合并测试页面 ${unique}`);
  await writeEditorTextAndWaitForSave(page, `共同版本 ${unique}`);
  await openHistoryPanel(page);

  await page.getByPlaceholder("new branch name").fill(sourceBranch);
  const branchResponse = page.waitForResponse(
    (response) => response.url().includes("/branches") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "branch" }).click();
  await branchResponse;
  await writeEditorTextAndWaitForSave(page, `来源分支内容 ${unique}`);

  const checkoutResponse = page.waitForResponse(
    (response) => response.url().includes("/branches/checkout") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "main", exact: true }).click();
  await checkoutResponse;
  await writeEditorTextAndWaitForSave(page, `当前分支内容 ${unique}`);

  const createMergeResponse = page.waitForResponse(
    (response) => response.url().endsWith("/merge-sessions") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Merge into current branch" }).click();
  await createMergeResponse;
  await expect(page.getByText("Studio Merge Mode")).toBeVisible();
  await expect(page.getByPlaceholder("The conversation has been paused during the merge and can be continued after submitting or abandoning the merge.")).toBeVisible();

  const resolutionResponse = page.waitForResponse(
    (response) => response.url().includes("/merge-sessions/") && response.request().method() === "PATCH"
  );
  await page.getByRole("button", { name: "source", exact: true }).first().click();
  await resolutionResponse;
  const editor = page.locator(".ProseMirror").first();
  await expect(editor).toContainText(`来源分支内容 ${unique}`);

  const finalDraft = `最终人工合并内容 ${unique}`;
  const draftSaveResponse = page.waitForResponse(
    (response) => response.url().includes("/merge-sessions/") && response.request().method() === "PATCH"
  );
  await editor.fill(finalDraft);
  await draftSaveResponse;

  await page.reload();
  await expect(page.getByText("Studio Merge Mode")).toBeVisible();
  await expect(page.locator(".ProseMirror").first()).toContainText(finalDraft);

  const submitResponse = page.waitForResponse(
    (response) => response.url().endsWith("/submit") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Commit merge" }).click();
  await submitResponse;
  await expect(page.getByText("Merge").first()).toBeVisible();
  await expect(page.getByRole("button", { name: sourceBranch, exact: true })).toBeVisible();
  await expect(page.locator(".ProseMirror").first()).toContainText(finalDraft);
});

test("DOCX import and export entry points complete without breaking the editor", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `DOCX 测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `DOCX 测试页面 ${unique}`);
  await writeEditorTextAndWaitForSave(page, `导入前内容 ${unique}`);

  await page.route("**/api/lessons/*/document/import-docx", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const importedText = `DOCX 导入内容 ${unique}`;
    const lesson = currentPackage.lessons[0];
    lesson.board_document = {
      ...lesson.board_document,
      content_text: importedText,
      content_html: `<p>${importedText}</p>`,
      content_json: {
        type: "doc",
        content: [{ type: "paragraph", content: [{ type: "text", text: importedText }] }],
      },
    };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(currentPackage) });
  });
  await page.route("**/api/lessons/*/document/export-docx", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      body: Buffer.from("openclass-docx-smoke"),
    });
  });

  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import DOCX" }).click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: "smoke.docx",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: Buffer.from("docx-smoke"),
  });

  await expect(page.locator(".ProseMirror").first()).toContainText(`DOCX 导入内容 ${unique}`);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export DOCX" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.docx$/);
});

test("exports, imports, replays, and forks a RIDOC lesson package", async ({ page }) => {
  const unique = Date.now();
  const firstVersion = `RIDOC 历史版本一 ${unique}`;
  const secondVersion = `RIDOC 历史版本二 ${unique}`;
  await enterAsMemberThroughApi(page);
  await page.getByLabel(/进入单独课程工作台|添加单独课程/).click();
  const createLessonMenuItem = page.getByRole("menuitem", { name: "Create new course" });
  if (await createLessonMenuItem.isVisible()) {
    await createLessonMenuItem.click();
  }
  await createLessonFromEmptyStudio(page, `RIDOC 测试页面 ${unique}`);
  await writeEditorTextAndWaitForSave(page, firstVersion);
  await writeEditorTextAndWaitForSave(page, secondVersion);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export course package", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.ridoc$/);
  const ridocStream = await download.createReadStream();
  const ridocChunks: Buffer[] = [];
  for await (const chunk of ridocStream) {
    ridocChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  await page.goto("/home");
  await page.getByLabel("Add individual courses").click();
  const importResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/workspace/import-ridoc") && response.request().method() === "POST"
  );
  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("menuitem", { name: "Import course files", exact: true }).click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: download.suggestedFilename(),
    mimeType: "application/vnd.openclass.ridoc+zip",
    buffer: Buffer.concat(ridocChunks),
  });
  await importResponse;

  const lessonCards = page
    .locator("[data-lesson-selection-root]")
    .filter({ hasText: `RIDOC 测试页面 ${unique}` });
  await expect(lessonCards).toHaveCount(2);
  await lessonCards.last().click();
  await expect(page.locator(".ProseMirror").first()).toContainText(secondVersion);
  await page.getByTitle("Expand right column").dispatchEvent("click");
  await expect(page.getByText("Revision history")).toBeVisible();
  await expect(page.getByText("RIDOC course package")).toBeVisible();
  await page.getByRole("button", { name: "play course" }).click();
  await page.getByRole("button", { name: "Pause playback" }).click();
  await expect(page.getByRole("button", { name: "Exit and continue learning" })).toBeVisible();
  await expect(page.getByText(/\/\d+$/)).toBeVisible();
  await page.getByRole("button", { name: "Next step" }).click();
  await page.getByRole("button", { name: "Next step" }).click();

  const branchResponse = page.waitForResponse(
    (response) => response.url().includes("/branches") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Branch from here" }).click();
  await branchResponse;
  await expect(page.getByRole("button", { name: "Exit and continue learning" })).toHaveCount(0);
  await expect(page.locator(".ProseMirror").first()).toContainText(firstVersion);
});

test("normalizes raw bold vector notation and math delimiters in the board editor", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `公式显示回归页面 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `公式显示回归课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, lessonTitle);

  const rawBlockFormula = "\\boldsymbol{x}=(x_1;x_2;\\cdots;x_d)";
  const rawInlineFormula = "Components of vector $$\\boldsymbol{x}$$";
  let injectedPackage: Record<string, unknown> | null = null;
  await page.route("**/api/course-package", async (route) => {
    if (!injectedPackage) {
      const authHeader = route.request().headers().authorization;
      const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const currentPackage = await currentPackageResponse.json();
      const lesson = currentPackage.lessons.find((candidate: { title: string }) => candidate.title === lessonTitle);
      lesson.board_document = {
        ...lesson.board_document,
        content_text: `${rawBlockFormula}\n\n${rawInlineFormula}`,
        content_html: `<p>${rawBlockFormula}</p><p>${rawInlineFormula}</p>`,
        content_json: {
          type: "doc",
          content: [
            { type: "paragraph", content: [{ type: "text", text: rawBlockFormula }] },
            { type: "paragraph", content: [{ type: "text", text: rawInlineFormula }] },
          ],
        },
      };
      injectedPackage = currentPackage;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(injectedPackage) });
  });

  await page.reload();

  const editor = page.locator(".ProseMirror").first();
  await expect(editor.locator("div.tiptap-mathematics-render")).toHaveCount(1);
  await expect(editor.locator("span.tiptap-mathematics-render")).toHaveCount(1);
  await expect(editor).not.toContainText("$$");
});

test("scrolls to and highlights the Board AI-authorized section being explained", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `讲解定位回归页面 ${unique}`;
  const targetHeading = `当前讲解的小节 ${unique}`;
  const targetSentence = `需要被荧光标记的讲解内容 ${unique}`;
  const nextHeading = `下一个小节 ${unique}`;
  const nextSentence = `不属于当前讲解范围的内容 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `讲解定位回归课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, lessonTitle);

  let injectedPackage: Record<string, unknown> | null = null;
  await page.route("**/api/course-package", async (route) => {
    if (!injectedPackage) {
      const authHeader = route.request().headers().authorization;
      const upstream = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const nextPackage = (await upstream.json()) as Record<string, unknown>;
      const lesson = (nextPackage.lessons as Array<Record<string, unknown>>)[0];
      const document = lesson.board_document as Record<string, unknown>;
      const historyGraph = lesson.history_graph as {
        commits: Array<Record<string, unknown>>;
        current_branch: string;
        branches: Record<string, { head_commit_id: string | null }>;
      };
      const branch = historyGraph.branches[historyGraph.current_branch];
      const fillerParagraphs = Array.from({ length: 48 }, (_, index) => `前置内容第 ${index + 1} 段 ${unique}`);
      const targetExcerpt = `## ${targetHeading}\n${targetSentence}`;
      const contentText = [
        `# ${lessonTitle}`,
        ...fillerParagraphs,
        targetExcerpt,
        `## ${nextHeading}\n${nextSentence}`,
      ].join("\n\n");
      const contentJson = {
        type: "doc",
        content: [
          {
            type: "heading",
            attrs: { level: 1 },
            content: [{ type: "text", text: lessonTitle }],
          },
          ...fillerParagraphs.map((text) => ({
            type: "paragraph",
            content: [{ type: "text", text }],
          })),
          {
            type: "heading",
            attrs: { level: 2 },
            content: [{ type: "text", text: targetHeading }],
          },
          {
            type: "paragraph",
            content: [{ type: "text", text: targetSentence }],
          },
          {
            type: "heading",
            attrs: { level: 2 },
            content: [{ type: "text", text: nextHeading }],
          },
          {
            type: "paragraph",
            content: [{ type: "text", text: nextSentence }],
          },
        ],
      };
      lesson.board_document = {
        ...document,
        content_text: contentText,
        content_html: "",
        content_json: contentJson,
      };
      const commitId = `commit_board_directed_explanation_${unique}`;
      historyGraph.commits.push({
        id: commitId,
        label: "Board-directed explanation",
        message: "Chatbot explained the Board AI-authorized section.",
        branch_name: historyGraph.current_branch,
        created_at: new Date().toISOString(),
        parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
        operations: [],
        snapshot: lesson.board_document,
        metadata: {
          kind: "board_directed_explanation",
          assistant_message: `正在讲解 ${targetHeading}`,
          board_task_route: "explain",
          resolved_focus: {
            source: "board",
            lesson_id: lesson.id,
            document_id: document.id,
            kind: "heading",
            heading_path: [targetHeading],
            excerpt: targetExcerpt,
            confidence: 1,
            display_label: targetHeading,
          },
          board_explanation_directive: {
            status: "approved",
            target_excerpt: targetExcerpt,
          },
        },
      });
      branch.head_commit_id = commitId;
      injectedPackage = nextPackage;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(injectedPackage) });
  });

  await page.reload();

  await expect(page.locator("article").filter({ hasText: `正在讲解 ${targetHeading}` })).toBeVisible();
  const teachingFocus = page.locator('[data-teaching-focus="true"]');
  const highlightedHeading = teachingFocus.filter({ hasText: targetHeading });
  const highlightedSentence = teachingFocus.filter({ hasText: targetSentence });
  await expect(highlightedHeading).toBeVisible();
  await expect(highlightedSentence).toBeVisible();
  await expect(highlightedHeading).toBeInViewport();
  await expect(highlightedSentence).toBeInViewport();
  await expect(teachingFocus.filter({ hasText: nextSentence })).toHaveCount(0);

  const boardScroll = page.locator('[data-board-scroll-container="true"]');
  await expect.poll(() => boardScroll.evaluate((element) => element.scrollTop)).toBeGreaterThan(100);
  await boardScroll.evaluate((element) => element.scrollTo({ top: 0, behavior: "auto" }));
  await expect.poll(() => boardScroll.evaluate((element) => element.scrollTop)).toBeLessThan(10);

  await page.getByRole("button", { name: /展开.*工具栏/ }).click();
  await expect.poll(() => boardScroll.evaluate((element) => element.scrollTop)).toBeLessThan(10);
  await expect(highlightedHeading).toBeAttached();
  await expect(highlightedSentence).toBeAttached();
});

test("restores future and legacy persisted chat shapes after refresh", async ({ page }) => {
  const unique = Date.now();
  const visibleFutureUser = `未来流程用户消息 ${unique}`;
  const visibleFutureAssistant = `未来流程 AI 回复 ${unique}`;
  const visibleLegacyAssistant = `旧课程 AI 回复 ${unique}`;
  const visibleRealtimeUser = `Realtime 用户消息 ${unique}`;
  const visibleRealtimeAssistant = `Realtime AI 回复 ${unique}`;
  const hiddenRealtimeToolUser = `Realtime 内部工具用户消息 ${unique}`;
  const hiddenRealtimeToolAssistant = `Realtime 内部工具 AI 回复 ${unique}`;
  const hiddenReadyAssistant = `内部 ready 回复 ${unique}`;
  const hiddenFrozenAssistant = `内部 frozen 回复 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `聊天刷新兼容课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `聊天刷新兼容页面 ${unique}`);

  let injectedPackage: Record<string, unknown> | null = null;
  await page.route("**/api/course-package", async (route) => {
    if (!injectedPackage) {
      const authHeader = route.request().headers().authorization;
      const upstream = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const nextPackage = (await upstream.json()) as Record<string, unknown>;
      const lesson = (nextPackage.lessons as Array<Record<string, unknown>>)[0];
      const historyGraph = lesson.history_graph as {
        commits: Array<Record<string, unknown>>;
        current_branch: string;
        branches: Record<string, { head_commit_id: string | null }>;
      };
      const branch = historyGraph.branches[historyGraph.current_branch];
      const appendCommit = (suffix: string, metadata: Record<string, unknown>) => {
        const commitId = `commit_${suffix}_${unique}`;
        historyGraph.commits.push({
          id: commitId,
          label: suffix,
          message: suffix,
          branch_name: historyGraph.current_branch,
          created_at: new Date().toISOString(),
          parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
          operations: [],
          snapshot: lesson.board_document,
          metadata,
        });
        branch.head_commit_id = commitId;
      };

      appendCommit("internal_ready", {
        kind: "future_requirement_lifecycle",
        history_node_kind: "chat",
        requirement_phase: "ready",
        assistant_message: hiddenReadyAssistant,
      });
      appendCommit("future_chat", {
        kind: "future_workflow_step",
        history_node_kind: "chat",
        user_message: visibleFutureUser,
        assistant_message: visibleFutureAssistant,
      });
      appendCommit("legacy_chat", {
        kind: "legacy_unknown_workflow_step",
        assistant_message: visibleLegacyAssistant,
      });
      appendCommit("realtime_user", {
        kind: "realtime_transcript",
        history_node_kind: "chat",
        interaction_channel: "realtime",
        realtime_client_event_id: `realtime_user_${unique}`,
        user_message: visibleRealtimeUser,
      });
      appendCommit("realtime_assistant", {
        kind: "realtime_transcript",
        history_node_kind: "chat",
        interaction_channel: "realtime",
        realtime_client_event_id: `realtime_assistant_${unique}`,
        assistant_message_source: "realtime",
        assistant_message: visibleRealtimeAssistant,
      });
      appendCommit("hidden_realtime_tool", {
        kind: "chat_flow",
        history_node_kind: "chat",
        chat_visibility: "hidden",
        interaction_channel: "realtime_tool",
        user_message: hiddenRealtimeToolUser,
        assistant_message: hiddenRealtimeToolAssistant,
      });
      appendCommit("internal_frozen", {
        kind: "future_requirement_lifecycle",
        history_node_kind: "chat",
        requirement_phase: "frozen",
        assistant_message: hiddenFrozenAssistant,
      });
      injectedPackage = nextPackage;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(injectedPackage) });
  });

  await page.reload();

  await expect(page.locator("article").filter({ hasText: visibleFutureUser })).toBeVisible();
  await expect(page.locator("article").filter({ hasText: visibleFutureAssistant })).toBeVisible();
  await expect(page.locator("article").filter({ hasText: visibleLegacyAssistant })).toBeVisible();
  await expect(page.locator("article").filter({ hasText: visibleRealtimeUser })).toBeVisible();
  await expect(page.locator("article").filter({ hasText: visibleRealtimeAssistant })).toBeVisible();
  await expect(page.locator("article").filter({ hasText: hiddenRealtimeToolUser })).toHaveCount(0);
  await expect(page.locator("article").filter({ hasText: hiddenRealtimeToolAssistant })).toHaveCount(0);
  await expect(page.locator("article").filter({ hasText: hiddenReadyAssistant })).toHaveCount(0);
  await expect(page.locator("article").filter({ hasText: hiddenFrozenAssistant })).toHaveCount(0);
});

test("keeps the learning requirement failure visible when the chat final event is missing", async ({ page }) => {
  const unique = Date.now();
  const userMessage = `继续整理我的学习需求 ${unique}`;
  const failureReason = "This round of learning requirements was not updated successfully, please try again the input just now.";
  let recoveredPackage: Record<string, unknown> | null = null;

  await enterAsGuest(page);
  await createPackageFromHome(page, `失败恢复测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `失败恢复测试页面 ${unique}`);

  await page.route("**/api/course-package", async (route) => {
    if (route.request().method() === "GET" && recoveredPackage) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(recoveredPackage),
      });
      return;
    }
    await route.continue();
  });
  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const lesson = currentPackage.lessons[0];
    const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
    const commitId = `commit_recovered_failure_${unique}`;
    lesson.history_graph.commits.push({
      id: commitId,
      label: "Learning requirement refinement failed",
      message: "Recorded a failed blank-board learning requirement refinement turn",
      branch_name: lesson.history_graph.current_branch,
      created_at: new Date().toISOString(),
      parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
      operations: [],
      snapshot: lesson.board_document,
      metadata: {
        kind: "learning_requirement_refinement",
        refinement_route: "refinement_failed",
        user_message: userMessage,
        assistant_message: "",
        assistant_message_source: "chatbot_empty",
        learning_requirement_operation_status: "failed",
        learning_requirement_operation_failure_reason: failureReason,
      },
    });
    branch.head_commit_id = commitId;
    recoveredPackage = currentPackage;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "event: phase data: {\"label\":\"Completing learning needs\"}",
    });
  });

  await page.getByPlaceholder("Send a message to OpenClass...").fill(userMessage);
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByRole("alert").filter({ hasText: failureReason })).toBeVisible();
});

test("restores persisted learning-intake assistant replies after a page refresh", async ({ page }) => {
  const unique = Date.now();
  const userMessage = `我想学习一个新知识点 ${unique}`;
  const assistantOpening = `这是已持久化的学习需求回复 ${unique}`;
  const assistantMessage = `${assistantOpening}\n$$\nx(t) = \\sin(2\\pi t)\n$$\n向量 $$\\boldsymbol{x}$$ 也应显示为行内公式。\n公式后面的说明仍应正常显示。`;
  const followUpSuggestions = [
    `用一个生活场景解释这个公式 ${unique}`,
    `进一步说明这个向量的含义 ${unique}`,
  ];
  let persistedPackage: Record<string, unknown> | null = null;

  await enterAsGuest(page);
  await createPackageFromHome(page, `聊天历史恢复测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `聊天历史恢复测试页面 ${unique}`);

  await page.route("**/api/course-package", async (route) => {
    if (!persistedPackage) {
      const authHeader = route.request().headers().authorization;
      const upstream = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const nextPackage = (await upstream.json()) as Record<string, unknown>;
      persistedPackage = nextPackage;
      const lesson = (nextPackage.lessons as Array<Record<string, unknown>>)[0];
      const historyGraph = lesson.history_graph as {
        commits: Array<Record<string, unknown>>;
        current_branch: string;
        branches: Record<string, { head_commit_id: string | null }>;
      };
      const branch = historyGraph.branches[historyGraph.current_branch];
      const commitId = `commit_persisted_learning_intake_${unique}`;
      historyGraph.commits.push({
        id: commitId,
        label: "Learning requirement refinement",
        message: "Recorded a learning-intake conversation turn",
        branch_name: historyGraph.current_branch,
        created_at: new Date().toISOString(),
        parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
        operations: [],
        snapshot: lesson.board_document,
        metadata: {
          kind: "learning_requirement_refinement",
          user_message: userMessage,
          assistant_message: assistantMessage,
          assistant_message_source: "chatbot_learning_intake",
          follow_up_suggestions: followUpSuggestions,
        },
      });
      branch.head_commit_id = commitId;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(persistedPackage) });
  });

  await page.reload();

  const chatSidebar = page.getByRole("complementary");
  await expect(chatSidebar.getByText(userMessage)).toBeVisible();
  await expect(chatSidebar.getByText(assistantOpening)).toBeVisible();
  await expect(chatSidebar.getByText("The description after the formula should still display normally.")).toBeVisible();
  await expect(chatSidebar.getByText("OK next")).toBeVisible();
  await expect(chatSidebar.getByRole("button", { name: followUpSuggestions[0] })).toBeVisible();
  await expect(chatSidebar.getByRole("button", { name: followUpSuggestions[1] })).toBeVisible();
  await expect(chatSidebar.locator(".katex-display")).toHaveCount(1);
  await expect(chatSidebar.locator(".katex")).toHaveCount(2);
  await expect(chatSidebar).not.toContainText("$$");
  await expect(chatSidebar).not.toContainText("BLOCKMATH");
});

test("does not show a second board-generation confirmation after learning requirements are ready", async ({ page }) => {
  const unique = Date.now();
  const userMessage = `直接开始生成板书 ${unique}`;
  const assistantMessage = `学习需求已准备好 ${unique}`;
  const requirementSheet = {
    theme: `聚焦学习主题 ${unique}`,
    learning_goal: `理解聚焦学习主题 ${unique}`,
    level: "getting Started",
    known_background: "",
    current_questions: [],
    learning_need_checklist: [],
    target_depth: "Build intuition",
    output_preference: "",
    boundary: "",
    board_scope: [],
    success_criteria: "",
    risk_notes: [],
    board_workflow: "generate_from_scratch",
    work_mode: "knowledge_board",
    granularity: "single_knowledge_point",
  };
  const clarityStatus = {
    progress: 100,
    label: "ready",
    reason: requirementSheet.learning_goal,
    missing_items: [],
    can_start: true,
    forced_start: false,
    summary: requirementSheet.learning_goal,
    key_facts: [],
    checklist: [],
    next_question: "",
    ready_for_board: true,
    work_mode: "knowledge_board",
    granularity: "single_knowledge_point",
  };

  await enterAsGuest(page);
  await createPackageFromHome(page, `无资料生成测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `无资料生成测试页面 ${unique}`);

  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const lesson = currentPackage.lessons[0];
    const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
    const commitId = `commit_ready_without_evidence_${unique}`;
    lesson.learning_requirements = requirementSheet;
    lesson.history_graph.commits.push({
      id: commitId,
      label: "Learning requirement refinement",
      message: "Recorded a ready learning requirement without source evidence",
      branch_name: lesson.history_graph.current_branch,
      created_at: new Date().toISOString(),
      parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
      operations: [],
      snapshot: lesson.board_document,
      metadata: {
        kind: "learning_requirement_refinement",
        user_message: userMessage,
        assistant_message: assistantMessage,
        assistant_message_source: "chatbot_learning_intake",
        learning_clarification_after: clarityStatus,
      },
    });
    branch.head_commit_id = commitId;
    const response = {
      chatbot_message: assistantMessage,
      agent_activity: [],
      learning_requirement_sheet: requirementSheet,
      active_requirement_sheet: requirementSheet,
      learning_clarification: clarityStatus,
      requirement_run_id: `reqrun_ready_without_evidence_${unique}`,
      requirement_version_id: `reqver_ready_without_evidence_${unique}`,
      requirement_phase: "ready",
      learning_requirement_operation_status: "succeeded",
      learning_requirement_operation_failure_reason: null,
      board_task_sheet: null,
      active_board_task_sheet: null,
      board_task_run_id: null,
      board_task_version_id: null,
      board_task_phase: null,
      board_task_questions: [],
      board_decision: { action: "no_change", reason: "Wait for the user to start generating blackboard writing" },
      needs_clarification: false,
      clarification_questions: [],
      requirement_cleared: false,
      board_document_operation_status: "none",
      board_document_operation_failure_reason: null,
      teaching_progress: null,
      course_package: currentPackage,
    };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: final\ndata: ${JSON.stringify(response)}\n\n`,
    });
  });

  await page.getByPlaceholder("Send a message to OpenClass...").fill(userMessage);
  const chatResponsePromise = page.waitForResponse(
    (response) => response.url().includes("/chat/stream") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Send message" }).click();
  expect((await chatResponsePromise).ok()).toBeTruthy();

  await expect(page.getByText("Learning needs have been clarified")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start generating blackboard writing" })).toHaveCount(0);
  await expect(page.getByText("The current round of data and evidence is being verified.")).toHaveCount(0);
});
