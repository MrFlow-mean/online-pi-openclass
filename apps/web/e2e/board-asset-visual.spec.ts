import { expect, test, type Page } from "@playwright/test";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8110";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const currentUrl = new URL(request.url());
    const targetBase = new URL(API_BASE_URL);
    if (currentUrl.origin === targetBase.origin) {
      await route.continue();
      return;
    }
    const headers = { ...request.headers() };
    delete headers.host;
    delete headers["content-length"];
    const upstream = await page.request.fetch(
      new URL(`${currentUrl.pathname}${currentUrl.search}`, targetBase).toString(),
      {
        method: request.method(),
        headers,
        data: request.postDataBuffer() ?? undefined,
      }
    );
    await route.fulfill({
      status: upstream.status(),
      headers: upstream.headers(),
      body: await upstream.body(),
    });
  });
  await page.route("**/api/ai-models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: [
          {
            provider: "openai_codex",
            model: "gpt-5.5",
            label: "Codex test model",
            capability: "text",
            enabled: true,
            configured: true,
            default: true,
            default_reasoning_effort: null,
            supported_reasoning_efforts: [],
            default_service_tier: null,
            service_tiers: [],
          },
        ],
        realtime: [
          {
            provider: "openai_codex",
            model: "realtime-unavailable",
            label: "Realtime unavailable",
            capability: "realtime",
            enabled: false,
            configured: false,
            default: true,
            default_reasoning_effort: null,
            supported_reasoning_efforts: [],
            default_service_tier: null,
            service_tiers: [],
          },
        ],
        defaults: {
          text: { provider: "openai_codex", model: "gpt-5.5" },
          realtime: { provider: "openai_codex", model: "realtime-unavailable" },
        },
      }),
    });
  });
});

async function enterAsGuest(page: Page) {
  await page.goto("/login?next=%2F");
  await page.getByRole("button", { name: /Continue as guest/ }).click();
  await expect(page).toHaveURL(/\/studio$/);
  await expect(page.getByText("This package is empty")).toBeVisible();
}

async function createLessonInGuestStudio(page: Page, unique: number) {
  await page.route(
    "**/api/lessons/generate*",
    async (route) => {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      await route.continue({
        postData: JSON.stringify({ ...payload, topic: `板书原图页面 ${unique}` }),
        headers: {
          ...route.request().headers(),
          "content-type": "application/json",
        },
      });
    },
    { times: 1 }
  );
  await page.getByRole("button", { name: "Create first page" }).click();
  await expect(page.locator(".ProseMirror")).toBeVisible();
}

async function serveBoardWithVisual(page: Page, unique: number, assetId: string, visualId: string) {
  const authToken = await page.evaluate(() =>
    window.sessionStorage.getItem("openclass.guest.auth.token") ||
    window.localStorage.getItem("openclass.auth.token")
  );
  const upstream = await page.request.get(`${API_BASE_URL}/api/course-package`, {
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
  });
  expect(upstream.ok()).toBeTruthy();
  const hydratedPackage = (await upstream.json()) as Record<string, unknown>;
  const lesson = (hydratedPackage.lessons as Array<Record<string, unknown>>)[0];
  const document = {
    ...(lesson.board_document as Record<string, unknown>),
    content_text: `图表前文 ${unique}\n增长趋势\n图表后文 ${unique}`,
    content_html: `<p>图表前文 ${unique}</p><section data-type="resource-visual-block" data-visual-id="${visualId}" data-board-asset-id="${assetId}" data-caption="增长趋势" data-source="图表资料 ${unique}" data-source-locator="page:7"></section><p>图表后文 ${unique}</p>`,
    content_json: {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: `图表前文 ${unique}` }] },
        {
          type: "resourceVisualBlock",
          attrs: {
            marker: `visual_marker_${unique}`,
            assetId,
            visualId,
            sourceIngestionId: `source_${unique}`,
            sourceChapterId: `chapter_${unique}`,
            sourceTitle: `图表资料 ${unique}`,
            sourceLocator: "page:7",
            kind: "chart",
            caption: "growing trend",
            source: `图表资料 ${unique}`,
            pageNo: 7,
            pageRange: "Page 7",
            recreationHtml: '<img src="x" onerror="window.__unsafeVisualHtmlExecuted=true">',
            originalSrc: "",
            originalAlt: "Growth trend original picture",
          },
        },
        { type: "paragraph", content: [{ type: "text", text: `图表后文 ${unique}` }] },
      ],
    },
  };
  lesson.board_document = document;
  const historyGraph = lesson.history_graph as {
    commits: Array<Record<string, unknown>>;
    current_branch: string;
    branches: Record<string, { head_commit_id: string | null }>;
  };
  const headId = historyGraph.branches[historyGraph.current_branch]?.head_commit_id;
  const head = historyGraph.commits.find((commit) => commit.id === headId);
  if (head) {
    head.snapshot = document;
  }
  await page.route("**/api/course-package", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(hydratedPackage) });
  });
}

test("loads the original board asset with auth and ignores recreation HTML", async ({ page }) => {
  const unique = Date.now();
  const assetId = `basset_visual_${unique}`;
  const visualId = `sourcevisual_${unique}`;
  let assetAuthorization = "";

  await enterAsGuest(page);
  await createLessonInGuestStudio(page, unique);
  await page.route(`**/api/board-assets/${assetId}/content`, async (route) => {
    assetAuthorization = route.request().headers().authorization ?? "";
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        "base64"
      ),
    });
  });
  await serveBoardWithVisual(page, unique, assetId, visualId);

  await page.reload();

  const block = page.locator(`section[data-type="resource-visual-block"][data-board-asset-id="${assetId}"]`);
  const image = block.locator("img");
  await expect(block).toBeVisible();
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute("src", /^blob:/);
  await expect(image).toHaveAttribute("alt", "Growth trend original picture");
  await expect(block).toContainText(`Source: 图表资料 ${unique} / Page 7`);
  await expect(block.locator(".word-editor__resource-visual-replica")).toHaveCount(0);
  await expect(block.getByRole("button", { name: /original image/i })).toHaveCount(0);
  expect(assetAuthorization).toMatch(/^Bearer /);
  expect(
    await page.evaluate(
      () => (window as Window & { __unsafeVisualHtmlExecuted?: boolean }).__unsafeVisualHtmlExecuted
    )
  ).toBeUndefined();
});

test("keeps a board asset load failure inside the visual block", async ({ page }) => {
  const unique = Date.now();
  const assetId = `basset_missing_${unique}`;
  const visualId = `sourcevisual_missing_${unique}`;

  await enterAsGuest(page);
  await createLessonInGuestStudio(page, unique);
  await page.route(`**/api/board-assets/${assetId}/content`, async (route) => {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Blackboard picture does not exist" }),
    });
  });
  await serveBoardWithVisual(page, unique, assetId, visualId);

  await page.reload();

  const block = page.locator(`section[data-type="resource-visual-block"][data-board-asset-id="${assetId}"]`);
  await expect(block).toBeVisible();
  await expect(block.locator(".word-editor__resource-visual-status--error")).toContainText("Blackboard picture does not exist");
  await expect(page.locator('main > div[role="alert"]')).toHaveCount(0);
  await expect(page.locator(".ProseMirror")).toContainText(`图表后文 ${unique}`);
});
