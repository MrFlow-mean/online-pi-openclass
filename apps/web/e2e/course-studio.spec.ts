import { expect, test, type Page } from "@playwright/test";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8110";

test.beforeEach(async ({ page }) => {
  const textModel = {
    provider: "openai_codex",
    model: "gpt-5.5",
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
  await page.getByRole("button", { name: /游客登录/ }).click();
  await expect(page.getByLabel("添加课程包")).toBeVisible();
}

async function createPackageFromHome(page: Page, title: string) {
  await page.getByLabel("添加课程包").click();
  await page.getByLabel("课程包名称").fill(title);
  const createPackageResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/packages") && response.request().method() === "POST"
  );
  await page.getByLabel("确认").click();
  await createPackageResponse;
  await expect(page.locator("[data-package-selection-root]").filter({ hasText: title }).first()).toBeVisible();
}

async function createLessonFromEmptyStudio(page: Page, title: string) {
  await page.goto("/studio");
  await expect(page.getByText("这个课程包还是空的")).toBeVisible();
  await page.getByRole("button", { name: "新建第一页" }).click();
  await page.getByLabel("第一页名称").fill(title);
  await page.getByLabel("确认").click();
  await expect(page.locator(".ProseMirror")).toBeVisible();
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
  await page.getByTitle("展开右侧栏").click();
  await expect(page.getByText("修订记录")).toBeVisible();
}

test("creates a package and lesson, edits the document, and persists a version", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `维护性测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `主流程页面 ${unique}`);

  await writeEditorTextAndWaitForSave(page, `第一版讲义内容 ${unique}`);
  await openHistoryPanel(page);

  await expect(page.getByText("Auto Save").first()).toBeVisible();
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
      deletedSourceIds.push(sourceId);
      visibleSources = visibleSources.filter((source) => source.id !== sourceId);
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
  await page.getByTitle("展开右侧栏").click();
  await page.getByRole("button", { name: "Sources" }).click();

  await expect(page.getByText("已上传 2 份资料")).toBeVisible();
  await expect(page.locator('[aria-label^="重命名资料 "]').first()).toHaveAttribute(
    "aria-label",
    `重命名资料 批量资料 B ${unique}`
  );
  await page.getByLabel("资料排序").selectOption("name_asc");
  await expect(page.locator('[aria-label^="重命名资料 "]').first()).toHaveAttribute(
    "aria-label",
    `重命名资料 批量资料 A ${unique}`
  );
  await page.getByLabel("资料排序").selectOption("uploaded_asc");
  await expect(page.locator('[aria-label^="重命名资料 "]').first()).toHaveAttribute(
    "aria-label",
    `重命名资料 批量资料 A ${unique}`
  );
  await page.getByLabel(`查看资料目录状态 批量资料 A ${unique}`).click();
  await page.getByLabel(`重新建立资料目录 批量资料 A ${unique}`).click();
  await expect.poll(() => legacyStructureRebuildRequests).toBe(1);
  expect(directoryCatalogRebuildRequests).toBe(0);
  await page.getByRole("button", { name: "批量管理" }).click();
  await expect(page.getByLabel(`选择资料 批量资料 A ${unique}`)).toBeVisible();
  await page.getByRole("button", { name: "全选", exact: true }).click();
  await expect(page.getByText("已选 2 / 2")).toBeVisible();

  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("确定删除选中的 2 份资料吗");
    await dialog.accept();
  });
  await page.getByRole("button", { name: "批量删除已选资料" }).click();

  await expect.poll(() => deletedSourceIds).toEqual(sourceRecords.map((source) => source.id));
  await expect(page.getByRole("button", { name: "批量管理" })).toHaveCount(0);
  await expect(page.getByText("拖拽文件到这里，或点击上传资料。")).toBeVisible();
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
  const deepSeekModel = {
    provider: "deepseek",
    model: "deepseek-chat",
    label: "DeepSeek V4 Flash",
    capability: "text",
    enabled: true,
    configured: true,
    default: false,
    default_reasoning_effort: null,
    supported_reasoning_efforts: [],
    default_service_tier: null,
    service_tiers: [],
  };
  await page.unroute("**/api/ai-models");
  await page.route("**/api/ai-models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: [solModel, lunaModel, defaultOnlyModel, deepSeekModel],
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
  let submittedSelection: Record<string, unknown> | null = null;
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
            title: `${rebuildRequests === 1 ? "重建后章节" : "再次重建章节"} ${unique}`,
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
    const payload = route.request().postDataJSON() as { selection?: Record<string, unknown> | null };
    submittedSelection = payload.selection ?? null;
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
    throw new Error("无法读取测试视口");
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
    throw new Error("聊天模型菜单未能完成视口定位");
  }
  expect(chatMenuBox.y + chatMenuBox.height).toBeLessThanOrEqual(chatButtonBox.y);
  expect(chatSubmenuBox.y + chatSubmenuBox.height).toBeLessThanOrEqual(chatButtonBox.y);
  expect(chatSubmenuBox.x + chatSubmenuBox.width).toBeLessThanOrEqual(viewport.width);
  await chatModelButton.click();
  await expect(chatModelMenu).toBeHidden();

  await page.getByTitle("展开右侧栏").click();
  await page.getByRole("button", { name: "Sources" }).click();

  const catalogModelButton = page.getByTestId("source-catalog-model-settings-button");
  const catalogModelMenu = page.getByTestId("source-catalog-model-settings-menu");
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 5\.6 Sol，推理强度 轻度，速度 标准/
  );
  await catalogModelButton.click();
  await expect(catalogModelMenu).toBeVisible();
  const triggerBox = await catalogModelButton.boundingBox();
  const menuBox = await catalogModelMenu.boundingBox();
  if (!triggerBox || !menuBox) {
    throw new Error("目录模型菜单未能完成视口定位");
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
    throw new Error("目录模型推理强度菜单未能完成视口定位");
  }
  expect(reasoningMenuBox.x + reasoningMenuBox.width).toBeLessThanOrEqual(menuBox.x);
  expect(reasoningMenuBox.x).toBeGreaterThanOrEqual(0);
  expect(reasoningMenuBox.y + reasoningMenuBox.height).toBeLessThanOrEqual(viewport.height);
  await reasoningMenu.getByRole("button", { name: "推理强度 高" }).click();
  await page.getByTestId("source-catalog-model-speed-row").click();
  await page
    .getByTestId("source-catalog-model-speed-menu")
    .getByRole("button", { name: "速度 快速" })
    .click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 5\.6 Sol，推理强度 高，速度 快速/
  );
  await catalogModelButton.click();
  await expect(catalogModelMenu).toBeHidden();
  await expect(catalogModelButton).toHaveAttribute("aria-expanded", "false");

  await expect.poll(() => batchCatalogRequests).toBe(1);
  await page.getByLabel(`查看资料目录 ${sourceTitle}`).click();
  await expect(page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) })).toBeVisible();
  await page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) }).click();
  await expect(page.getByRole("button", { name: new RegExp(`^1\\.1 ${partialChapterTitle}`) })).toBeVisible();
  await expect(page.getByRole("button", { name: /引用章节到输入框/ })).toHaveCount(1);
  expect(singleCatalogRequests).toBe(0);

  await page.getByRole("button", { name: "History" }).click();
  await page.getByRole("button", { name: "Sources" }).click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 5\.6 Sol，推理强度 高，速度 快速/
  );
  await page.getByLabel(`查看资料目录 ${sourceTitle}`).click();
  await expect(page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) })).toBeVisible();
  expect(batchCatalogRequests).toBe(1);
  expect(singleCatalogRequests).toBe(0);

  await page.getByRole("button", { name: `引用章节到输入框 1 ${chapterTitle}` }).click();
  await page.getByPlaceholder("基于引用章节继续提问").fill("请基于这个章节生成板书");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect.poll(() => submittedSelection).not.toBeNull();
  expect(submittedSelection).toMatchObject({
    source_ingestion_id: sourceId,
    source_chapter_id: verifiedChapter.id,
    catalog_version: 3,
    source_content_hash: initialContentHash,
    source_page_start: 12,
    source_page_end: 18,
    source_range: {
      kind: "pdf_pages",
      start: 12,
      end: 18,
      end_inclusive: true,
    },
  });

  const replacementContentHash = `replacement-hash-${unique}`;
  advertisedContentHash = replacementContentHash;
  reportedStructureStatus = "ready";
  staleSingleCatalogResponsesRemaining = 2;
  delaySingleCatalogResponseAt = 2;
  servedCatalog = {
    ...catalog,
    source_content_hash: replacementContentHash,
    chapters: catalog.chapters.map((chapter) => ({
      ...chapter,
      source_content_hash: replacementContentHash,
    })),
  };
  await expect.poll(() => singleCatalogRequests, { timeout: 7_000 }).toBe(2);
  await expect.poll(() => delayedSingleCatalogRequests).toBe(1);

  await page.getByLabel(`重新建立资料目录 ${sourceTitle}`).click();
  await expect.poll(() => rebuildRequests).toBe(1);
  expect(rebuildPostData).toContain('name="catalog_model"');
  expect(rebuildPostData).toContain('"provider":"openai_codex"');
  expect(rebuildPostData).toContain('"model":"gpt-5.6-sol"');
  expect(rebuildPostData).toContain('"reasoning_effort":"high"');
  expect(rebuildPostData).toContain('"service_tier":"priority"');
  releaseDelayedSingleCatalog();
  await expect.poll(() => completedSingleCatalogResponses).toBe(2);
  await expect(page.getByRole("button", { name: new RegExp(`^1 重建后章节 ${unique}`) })).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(`^1 ${chapterTitle}`) })).toHaveCount(0);

  await catalogModelButton.click();
  await page.getByTestId("source-catalog-model-model-row").click();
  await page
    .getByTestId("source-catalog-model-model-menu")
    .getByRole("button", { name: "选择模型 5.6 Luna" })
    .click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 5\.6 Luna，推理强度 中，速度 标准/
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
    .getByRole("button", { name: "选择模型 DeepSeek V4 Flash" })
    .click();
  await expect(catalogModelButton).toHaveAccessibleName(
    /目录提取模型设置，当前 DeepSeek V4 Flash，推理强度 默认，速度 标准/
  );
  uploadPostData = "";
  await page.getByTestId("source-file-input").setInputFiles({
    name: `catalog-deepseek-model-${unique}.pdf`,
    mimeType: "application/pdf",
    buffer: Buffer.from("deepseek catalog model upload"),
  });
  await expect.poll(() => uploadPostData).toContain('name="catalog_model"');
  expect(uploadPostData).toContain('"provider":"deepseek"');
  expect(uploadPostData).toContain('"model":"deepseek-chat"');

  await catalogModelButton.click();
  await page.getByTestId("source-catalog-model-model-row").click();
  await page
    .getByTestId("source-catalog-model-model-menu")
    .getByRole("button", { name: "选择模型 Default only test model" })
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

  await page.getByLabel("新建页面").click();
  await page.getByLabel("新页面名称").fill(secondTitle);
  await page.getByLabel("确认").click();
  await expect(page.getByRole("button", { name: `${secondTitle} main` })).toBeVisible();

  await page.getByRole("button", { name: `${firstTitle} main` }).click();
  const editor = page.locator(".ProseMirror").first();
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.getByRole("button", { name: "引用到输入框" }).click();
  await expect(page.getByLabel("移除引用")).toBeVisible();
  await expect(page.getByText(referencedText, { exact: false }).last()).toBeVisible();
  await page.getByPlaceholder("基于选中内容继续追问").click();
  await expect(page.getByLabel("移除引用")).toBeVisible();

  await page.getByRole("button", { name: `${secondTitle} main` }).click();
  await expect(page.getByLabel("移除引用")).toHaveCount(0);

  await page.getByRole("button", { name: `${firstTitle} main` }).click();
  await expect(page.getByLabel("移除引用")).toBeVisible();
  await expect(page.getByText(referencedText, { exact: false }).last()).toBeVisible();
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
        title: "平行边四边形",
        summary: "用一组代表性坐标呈现题目中的平行关系。",
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
        steps: ["AB 与 CD 使用相同方向的线段表示。"],
        source_excerpt: referencedText,
      }),
    });
  });

  const editor = page.locator(".ProseMirror").first();
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.getByRole("button", { name: "引用到图形" }).click();

  const geometryPanel = page.locator("[data-geometry-generation-panel]");
  await expect(page.getByRole("button", { name: "Geometry" })).toBeVisible();
  await expect(geometryPanel.getByText("几何图形生成")).toBeVisible();
  await expect(geometryPanel.getByText(referencedText, { exact: true })).toBeVisible();
  await expect(geometryPanel.getByText("添加照片和文件")).toBeVisible();
  await geometryPanel.getByRole("button", { name: "添加附件" }).click();
  await expect(page.getByRole("menuitem", { name: "添加图片" })).toBeVisible();
  await page.getByTestId("geometry-image-input").setInputFiles({
    name: fileName,
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64"
    ),
  });
  await expect(geometryPanel.getByLabel("已添加附件")).toContainText(fileName);
  await geometryPanel.getByRole("button", { name: "生成图形" }).click();

  await expect(page.getByRole("img", { name: "平行边四边形交互图形" })).toBeVisible();
  await expect(page.getByText("3D · 拖动旋转")).toBeVisible();
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

  await page.getByRole("button", { name: "添加附件" }).click();
  const attachmentButtonBox = await page.getByRole("button", { name: "添加附件" }).boundingBox();
  const textModelPickerBox = await page.getByTestId("codex-model-settings-button").boundingBox();
  const attachmentMenuBox = await page.getByRole("menu", { name: "添加内容" }).boundingBox();
  expect(attachmentButtonBox).not.toBeNull();
  expect(textModelPickerBox).not.toBeNull();
  expect(attachmentMenuBox).not.toBeNull();
  expect(attachmentButtonBox?.y ?? 0).toBeGreaterThanOrEqual(
    (textModelPickerBox?.y ?? 0) + (textModelPickerBox?.height ?? 0)
  );
  expect((attachmentMenuBox?.y ?? 0) + (attachmentMenuBox?.height ?? 0)).toBeLessThanOrEqual(
    textModelPickerBox?.y ?? 0
  );
  await expect(page.getByRole("menuitem", { name: "添加图片" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "添加文件" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "展开手写板" })).toBeVisible();
  await page.getByTestId("chat-image-input").setInputFiles({
    name: fileName,
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64"
    ),
  });

  await expect(page.getByLabel("已添加附件")).toContainText(fileName);
  await expect(page.getByRole("button", { name: `移除附件 ${fileName}` })).toBeVisible();
  await page.getByPlaceholder("给 OpenClass 发消息...").fill("请结合这张图回答");
  const chatRequestPromise = page.waitForRequest(
    (request) => request.url().includes("/chat/stream") && request.method() === "POST"
  );
  await page.getByRole("button", { name: "发送消息" }).click();
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

  await page.getByRole("button", { name: "添加附件" }).click();
  await page.getByRole("menuitem", { name: "展开手写板" }).click();
  await expect(page.getByRole("dialog", { name: "手写板" })).toBeVisible();

  const addButton = page.getByRole("button", { name: "添加到消息" });
  await expect(addButton).toBeDisabled();
  const canvas = page.getByLabel("手写输入画板");
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

  await expect(page.getByRole("dialog", { name: "手写板" })).toBeHidden();
  await expect(page.getByLabel("已添加附件")).toContainText(fileName);
});

test("places the create control first and orders lesson tabs from newest to oldest", async ({ page }) => {
  const unique = Date.now();
  const firstTitle = `较早课程 ${unique}`;
  const secondTitle = `最近课程 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `课程顺序测试包 ${unique}`);
  await createLessonFromEmptyStudio(page, firstTitle);

  await page.getByLabel("新建页面").click();
  await page.getByLabel("新页面名称").fill(secondTitle);
  await page.getByLabel("确认").click();

  const lessonTabList = page
    .getByRole("navigation")
    .filter({ has: page.getByRole("button", { name: `${secondTitle} main` }) });
  const lessonTabs = lessonTabList.locator(":scope > button");
  await expect(lessonTabs.nth(0)).toHaveAccessibleName("新建页面");
  await expect(lessonTabs.nth(1)).toHaveAccessibleName(`${secondTitle} main`);
  await expect(lessonTabs.nth(2)).toHaveAccessibleName(`${firstTitle} main`);
});

test("uses the top-right profile avatar as the only account menu on the home page", async ({ page }) => {
  await enterAsGuest(page);

  const accountMenu = page.locator("[data-account-menu-root]");
  await expect(accountMenu).toHaveCount(1);
  await page.getByRole("button", { name: "开放课堂用户头像" }).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "登录以保存" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "结束游客访问" })).toBeVisible();
});

test("collapses course package and standalone lesson lists independently", async ({ page }) => {
  await enterAsGuest(page);

  const packageList = page.locator("#learning-home-course-packages");
  const standaloneList = page.locator("#learning-home-standalone-lessons");
  const collapsePackages = page.getByLabel("收起课程包");
  const collapseStandaloneLessons = page.getByLabel("收起单独课程");

  await expect(packageList).toBeVisible();
  await expect(packageList).toHaveCSS("overflow-y", "auto");
  await expect(standaloneList).toBeVisible();

  await collapsePackages.click();
  await expect(packageList).toBeHidden();
  await expect(page.getByLabel("展开课程包")).toHaveAttribute("aria-expanded", "false");
  await expect(standaloneList).toBeVisible();

  await collapseStandaloneLessons.click();
  await expect(standaloneList).toBeHidden();
  await expect(page.getByLabel("展开单独课程")).toHaveAttribute("aria-expanded", "false");
});

test("exports and imports a RIDOC file as a standalone lesson", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `主页课程包入口 ${unique}`;
  await enterAsGuest(page);
  await page.getByLabel("添加单独课程").click();
  await expect(page.getByRole("menuitem", { name: "导入课程文件" })).toBeVisible();
  await page.getByRole("menuitem", { name: "新建课程" }).click();
  await createLessonFromEmptyStudio(page, lessonTitle);
  await writeEditorTextAndWaitForSave(page, `主页导出内容 ${unique}`);
  await page.goto("/home");

  const lessonCard = page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle });
  await lessonCard.getByLabel("打开课程操作菜单").click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出课程包", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.ridoc$/);
  const ridocStream = await download.createReadStream();
  const ridocChunks: Buffer[] = [];
  for await (const chunk of ridocStream) {
    ridocChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  await page.getByLabel("添加单独课程").click();
  const importResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/workspace/import-ridoc") && response.request().method() === "POST"
  );
  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("menuitem", { name: "导入课程文件", exact: true }).click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: download.suggestedFilename(),
    mimeType: "application/vnd.openclass.ridoc+zip",
    buffer: Buffer.concat(ridocChunks),
  });
  await importResponse;

  await expect(page.locator("[data-lesson-selection-root]").filter({ hasText: lessonTitle })).toHaveCount(2);
});

test("localizes the empty course package page in English", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `English empty package ${unique}`);
  await page.goto("/studio");

  await expect(page.getByText("这个课程包还是空的")).toBeVisible();
  await setInterfaceLanguage(page, "en");
  await expect(page.getByRole("heading", { name: "This package is empty" })).toBeVisible();
  await expect(page.getByText("The tab bar above is this package's page area.")).toBeVisible();
  await page.getByRole("button", { name: "Create first page" }).click();
  await expect(page.getByLabel("First page name")).toHaveAttribute(
    "placeholder",
    "Course intro / Lecture 1 / Practice notes"
  );
  await expect(page.getByLabel("Confirm")).toBeVisible();
  await expect(page.getByLabel("Cancel")).toBeVisible();
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

  await page.getByPlaceholder("新分支名").fill(sourceBranch);
  const branchResponse = page.waitForResponse(
    (response) => response.url().includes("/branches") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "开分支" }).click();
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
  await page.getByRole("button", { name: "合并到当前分支" }).click();
  await createMergeResponse;
  await expect(page.getByText("Studio Merge Mode")).toBeVisible();
  await expect(page.getByPlaceholder("合并期间对话已暂停，提交或放弃合并后可继续")).toBeVisible();

  const resolutionResponse = page.waitForResponse(
    (response) => response.url().includes("/merge-sessions/") && response.request().method() === "PATCH"
  );
  await page.getByRole("button", { name: "来源", exact: true }).first().click();
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
  await page.getByRole("button", { name: "提交合并" }).click();
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
  await page.getByRole("button", { name: "导入 DOCX" }).click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: "smoke.docx",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: Buffer.from("docx-smoke"),
  });

  await expect(page.locator(".ProseMirror").first()).toContainText(`DOCX 导入内容 ${unique}`);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 DOCX" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.docx$/);
});

test("exports, imports, replays, and forks a RIDOC lesson package", async ({ page }) => {
  const unique = Date.now();
  const firstVersion = `RIDOC 历史版本一 ${unique}`;
  const secondVersion = `RIDOC 历史版本二 ${unique}`;
  await enterAsGuest(page);
  await page.getByLabel(/进入单独课程工作台|添加单独课程/).click();
  const createLessonMenuItem = page.getByRole("menuitem", { name: "新建课程" });
  if (await createLessonMenuItem.isVisible()) {
    await createLessonMenuItem.click();
  }
  await createLessonFromEmptyStudio(page, `RIDOC 测试页面 ${unique}`);
  await writeEditorTextAndWaitForSave(page, firstVersion);
  await writeEditorTextAndWaitForSave(page, secondVersion);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出课程包", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.ridoc$/);
  const ridocStream = await download.createReadStream();
  const ridocChunks: Buffer[] = [];
  for await (const chunk of ridocStream) {
    ridocChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  await page.goto("/home");
  await page.getByLabel("添加单独课程").click();
  const importResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/workspace/import-ridoc") && response.request().method() === "POST"
  );
  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("menuitem", { name: "导入课程文件", exact: true }).click();
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
  await page.getByTitle("展开右侧栏").dispatchEvent("click");
  await expect(page.getByText("修订记录")).toBeVisible();
  await expect(page.getByText("RIDOC 课程包")).toBeVisible();
  await page.getByRole("button", { name: "播放课程" }).click();
  await page.getByRole("button", { name: "暂停播放" }).click();
  await expect(page.getByRole("button", { name: "退出并继续学习" })).toBeVisible();
  await expect(page.getByText(/\/\d+$/)).toBeVisible();
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "下一步" }).click();

  const branchResponse = page.waitForResponse(
    (response) => response.url().includes("/branches") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "从这里分叉" }).click();
  await branchResponse;
  await expect(page.getByRole("button", { name: "退出并继续学习" })).toHaveCount(0);
  await expect(page.locator(".ProseMirror").first()).toContainText(firstVersion);
});

test("normalizes raw bold vector notation and math delimiters in the board editor", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `公式显示回归页面 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `公式显示回归课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, lessonTitle);

  const rawBlockFormula = "\\boldsymbol{x}=(x_1;x_2;\\cdots;x_d)";
  const rawInlineFormula = "向量 $$\\boldsymbol{x}$$ 的分量";
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
  const failureReason = "本轮学习需求没有成功更新，请重试刚才的输入。";
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
      body: 'event: phase\ndata: {"label":"正在整理学习需求"}\n\n',
    });
  });

  await page.getByPlaceholder("给 OpenClass 发消息...").fill(userMessage);
  await page.getByRole("button", { name: "发送消息" }).click();

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
  await expect(chatSidebar.getByText("公式后面的说明仍应正常显示。")).toBeVisible();
  await expect(chatSidebar.getByText("接下来可以")).toBeVisible();
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
    level: "入门",
    known_background: "",
    current_questions: [],
    learning_need_checklist: [],
    target_depth: "建立直觉",
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
  await page.route("**/api/lessons/*/evidence/pending", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "null" });
  });
  const initialEvidenceResponse = page.waitForResponse(
    (response) => response.url().includes("/evidence/pending") && response.request().method() === "GET"
  );
  await createLessonFromEmptyStudio(page, `无资料生成测试页面 ${unique}`);
  await initialEvidenceResponse;

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
      board_decision: { action: "no_change", reason: "等待用户开始生成板书" },
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

  await page.getByPlaceholder("给 OpenClass 发消息...").fill(userMessage);
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page.getByText("学习需求已清晰")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "开始生成板书" })).toHaveCount(0);
  await expect(page.getByText("正在核对本轮资料证据。")).toHaveCount(0);
});
