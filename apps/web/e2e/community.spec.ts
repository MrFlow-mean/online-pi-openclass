import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";


const readyIntegration = {
  provider: "answer",
  public_url: "https://community.example.com",
  entry_url: "https://community.example.com/answer/api/v1/connector/login/basic",
  available: true,
  sso_enabled: true,
  setup_required: false,
};

const registeredUser = {
  id: "user-author",
  email: "author@example.com",
  role: "user",
  display_name: "Learner A",
  avatar_url: null,
  created_at: "2026-07-27T10:00:00+00:00",
  last_login_at: "2026-07-27T10:00:00+00:00",
  auth_identities: [],
};

const answerSsoBridge = readFileSync(
  resolve(process.cwd(), "../../deploy/answer/openclass-sso-bridge.js"),
  "utf8",
);
const answerRidocBridge = readFileSync(
  resolve(process.cwd(), "../../deploy/answer/openclass-ridoc-bridge.js"),
  "utf8",
);


function crc32(content: Buffer) {
  let crc = 0xffffffff;
  for (const byte of content) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}


function storedZip(entries: Record<string, string>) {
  const localParts: Buffer[] = [];
  const centralParts: Buffer[] = [];
  let offset = 0;
  for (const [name, text] of Object.entries(entries)) {
    const nameBytes = Buffer.from(name);
    const content = Buffer.from(text);
    const checksum = crc32(content);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(checksum, 14);
    local.writeUInt32LE(content.length, 18);
    local.writeUInt32LE(content.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    localParts.push(local, nameBytes, content);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(checksum, 16);
    central.writeUInt32LE(content.length, 20);
    central.writeUInt32LE(content.length, 24);
    central.writeUInt16LE(nameBytes.length, 28);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, nameBytes);
    offset += local.length + nameBytes.length + content.length;
  }
  const centralDirectory = Buffer.concat(centralParts);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(entries).length, 8);
  end.writeUInt16LE(Object.keys(entries).length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...localParts, centralDirectory, end]);
}


test("sends a registered OpenClass user through Answer single sign-on", async ({ page }) => {
  const now = "2026-07-25T10:00:00+00:00";
  await page.context().addCookies([
    { name: "openclass.auth.token", value: "community-test-token", domain: "127.0.0.1", path: "/" },
  ]);
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      id: "user-author",
      email: "author@example.com",
      role: "user",
      display_name: "Learner A",
      avatar_url: null,
      created_at: now,
      last_login_at: now,
      auth_identities: [],
    }),
  }));
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(readyIntegration),
  }));
  await page.route("https://community.example.com/**", (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<title>OpenClass Community</title><h1>Answer</h1>",
  }));

  await page.goto("/community");

  await expect(page).toHaveURL(readyIntegration.entry_url);
  await expect(page.getByRole("heading", { name: "Answer" })).toBeVisible();
});


test("preserves a same-origin history-node draft through Answer single sign-on", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const publicUrl = `${baseURL}/community`;
  const entryUrl = `${publicUrl}/answer/api/v1/connector/login/basic`;
  const referenceDraft = `> [Course history reference · Open](${baseURL}/courses/shared/lesson/lesson-history?history_node=commit-history)`;
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(registeredUser),
  }));
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ...readyIntegration, public_url: publicUrl, entry_url: entryUrl }),
  }));
  await page.route(entryUrl, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<h1>Answer SSO</h1>",
  }));

  await page.goto("/community?reference=history_node&lesson_id=lesson-history&history_node=commit-history");

  await expect(page).toHaveURL(entryUrl);
  const redirectPath = await page.evaluate(() => window.localStorage.getItem("_a_rp_"));
  expect(redirectPath).toBeTruthy();
  const storedPrefill = new URL(redirectPath!, "http://answer.local").searchParams.get("prefill");
  expect(storedPrefill).toBeTruthy();
  expect(decodeURIComponent(storedPrefill!)).toBe(referenceDraft);
  expect(referenceDraft).not.toContain("title:");
  expect(referenceDraft).not.toContain("Class text");
});


test("sends a cross-origin history-node reference to the configured Answer site", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const referenceDraft = `> [Course history reference · Open](${baseURL}/courses/shared/lesson/lesson-history?history_node=commit-history)`;
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(registeredUser),
  }));
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(readyIntegration),
  }));
  await page.route("https://community.example.com/questions/add**", (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<h1>Prefilled Answer draft</h1>",
  }));

  await page.goto("/community?reference=history_node&lesson_id=lesson-history&history_node=commit-history");

  await expect(page).toHaveURL((url) => (
    url.origin === readyIntegration.public_url
    && url.pathname === "/questions/add"
    && decodeURIComponent(url.searchParams.get("prefill") ?? "") === referenceDraft
  ));
  await expect(page.getByRole("heading", { name: "Prefilled Answer draft" })).toBeVisible();
});


test("sends an anonymous reader to the public Answer site", async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Not logged in" }),
  }));
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(readyIntegration),
  }));
  await page.route("https://community.example.com/", (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<title>OpenClass Community</title><h1>Public Answer</h1>",
  }));

  await page.goto("/community");

  await expect(page).toHaveURL(readyIntegration.public_url);
  await expect(page.getByRole("heading", { name: "Public Answer" })).toBeVisible();
});


test("keeps a same-origin OpenClass entry separate from the public Answer mount", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const publicUrl = `${baseURL}/community`;
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Not logged in" }),
  }));
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ...readyIntegration, public_url: publicUrl }),
  }));
  await page.route(`${publicUrl}/`, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<title>OpenClass Community</title><h1>Public Answer</h1>",
  }));

  await page.goto("/community");

  await expect(page).toHaveURL(`${publicUrl}/`);
  await expect(page.getByRole("heading", { name: "Public Answer" })).toBeVisible();
});


test("repairs a missing Answer session from a same-origin OpenClass session", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ id: "user-author", role: "user" }),
  }));
  await page.route(/\/community\/$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)}};</script><script>${answerSsoBridge}</script><h1>Anonymous Answer</h1>`,
  }));
  await page.route(/\/community$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<h1>OpenClass SSO entry</h1>",
  }));

  await page.goto("/community/");

  await expect(page).toHaveURL(entryUrl);
  await expect(page.getByRole("heading", { name: "OpenClass SSO entry" })).toBeVisible();
});


test("keeps an existing Answer session without restarting SSO", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  let openClassSessionChecks = 0;
  await page.addInitScript(() => window.localStorage.setItem("_a_ltk_", "valid-answer-token"));
  await page.route("**/answer/api/v1/user/info", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: { username: "learner" } }),
  }));
  await page.route("**/api/auth/me", (route) => {
    openClassSessionChecks += 1;
    return route.fulfill({ status: 500 });
  });
  await page.route(/\/community\/$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)}};</script><script>${answerSsoBridge}</script><h1>Authenticated Answer</h1>`,
  }));

  await page.goto("/community/");

  await expect(page).toHaveURL(`${entryUrl}/`);
  await expect(page.getByRole("heading", { name: "Authenticated Answer" })).toBeVisible();
  expect(openClassSessionChecks).toBe(0);
});


test("renders a shared history-node link as a fully clickable reference card", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  const targetUrl = `${baseURL}/courses/shared/lesson/lesson-history?history_node=commit-history`;
  await page.route(/\/community\/$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)}};</script><script>${answerSsoBridge}</script><blockquote><p><a href="${targetUrl}">Course history reference · Open</a></p></blockquote>`,
  }));
  await page.route(targetUrl, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<h1>Referenced history node</h1>",
  }));

  await page.goto("/community/");

  const card = page.locator("blockquote.openclass-history-reference");
  await expect(card).toHaveAttribute("role", "link");
  await expect(card).toHaveAttribute("tabindex", "0");
  await card.click({ position: { x: 8, y: 8 } });
  await expect(page).toHaveURL(targetUrl);
  await expect(page.getByRole("heading", { name: "Referenced history node" })).toBeVisible();
});


test("adds a RIDOC course file to an Answer draft without filling the post title", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  const ridoc = storedZip({
    "manifest.json": JSON.stringify({
      spec_version: "1.0",
      profile: "learning.lesson",
      media_type: "application/vnd.openclass.ridoc+zip",
      lesson: { title: "Replayable learning sessions", summary: "This is a course introduction that can be continued and branched off." },
      capabilities: { playback: true, continue: true, fork: true },
    }),
    "history/graph.json": "{}",
    "evidence/index.json": "{}",
    "integrity/checksums.json": "{}",
  });
  let uploadAuthorization = "";
  await page.addInitScript(() => window.localStorage.setItem("_a_ltk_", "answer-token"));
  await page.route("**/answer/api/v1/file", (route) => {
    uploadAuthorization = route.request().headers().authorization || "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: `${baseURL}/uploads/shared-course.ridoc` }),
    });
  });
  await page.route(/\/community\/questions\/add$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html; charset=utf-8",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)}};</script><script>${answerRidocBridge}</script><label>Title<input aria-label="Title"></label><div><textarea aria-label="Content"></textarea></div>`,
  }));

  await page.goto("/community/questions/add");

  await expect(page.getByText("Add a RIDOC course file")).toBeVisible();
  await page.locator('input[type="file"][accept*=".ridoc"]').setInputFiles({
    name: "shared-course.ridoc",
    mimeType: "application/vnd.openclass.ridoc+zip",
    buffer: ridoc,
  });
  const body = page.getByLabel("content");
  await expect(body).toHaveValue(/OpenClass RIDOC course file/);
  await expect(page.getByLabel("title")).toHaveValue("");
  await expect(page.getByText("Added: Replayable Learning Sessions")).toBeVisible();
  expect(uploadAuthorization).toBe("answer-token");
  const markdown = await body.inputValue();
  const encodedMetadata = markdown.match(/#openclass-ridoc=([^\)]+)/)?.[1];
  expect(encodedMetadata).toBeTruthy();
  const metadata = JSON.parse(Buffer.from(encodedMetadata!, "base64url").toString("utf8"));
  expect(metadata).toMatchObject({
    title: "Replayable learning sessions",
    summary: "This is a course introduction that can be continued and branched off.",
  });
});


test("rejects a renamed non-RIDOC file before uploading it", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  let uploadRequests = 0;
  await page.addInitScript(() => window.localStorage.setItem("_a_ltk_", "answer-token"));
  await page.route("**/answer/api/v1/file", (route) => {
    uploadRequests += 1;
    return route.fulfill({ status: 500 });
  });
  await page.route(/\/community\/questions\/add$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html; charset=utf-8",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)}};</script><script>${answerRidocBridge}</script><label>Title<input aria-label="Title"></label><div><textarea aria-label="Content"></textarea></div>`,
  }));

  await page.goto("/community/questions/add");
  await page.locator('input[type="file"][accept*=".ridoc"]').setInputFiles({
    name: "renamed.ridoc",
    mimeType: "application/vnd.openclass.ridoc+zip",
    buffer: Buffer.from("not a zip archive"),
  });

  await expect(page.getByText("File is not a valid RIDOC ZIP archive")).toBeVisible();
  await expect(page.getByLabel("content")).toHaveValue("");
  await expect(page.getByLabel("title")).toHaveValue("");
  expect(uploadRequests).toBe(0);
});


test("renders a RIDOC attachment as a clickable course introduction card", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  const metadata = Buffer.from(JSON.stringify({
    version: 1,
    title: "Course card title",
    summary: "Introductions in course cards are provided by the RIDOC checklist.",
    fileName: "course.ridoc",
    sizeBytes: 2048,
    capabilities: ["Playable", "Can continue", "bifurcatable"],
  })).toString("base64url");
  const attachmentUrl = `${baseURL}/uploads/course.ridoc#openclass-ridoc=${metadata}`;
  const downloadUrl = `${baseURL}/uploads/course.ridoc`;
  await page.route(/\/community\/questions\/course-card$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html; charset=utf-8",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)}};</script><script>${answerRidocBridge}</script><blockquote><p><a href="${attachmentUrl}">OpenClass RIDOC course file</a></p></blockquote>`,
  }));
  await page.route(downloadUrl, (route) => route.fulfill({
    status: 200,
    contentType: "application/vnd.openclass.ridoc+zip",
    headers: { "Content-Disposition": 'attachment; filename="course.ridoc"' },
    body: "ridoc-download",
  }));

  await page.goto("/community/questions/course-card");

  const card = page.locator("blockquote.openclass-ridoc-card");
  await expect(card).toHaveAttribute("role", "link");
  await expect(card).toContainText("Course card title");
  await expect(card).toContainText("Introductions in course cards are provided by the RIDOC checklist.");
  await expect(card).toContainText("2 KB");
  await expect(card).toContainText("Playable");
  const downloadPromise = page.waitForEvent("download");
  await card.click({ position: { x: 8, y: 8 } });
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("course.ridoc");
});


test("replaces a blocked external Answer avatar with a local fallback", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  await page.route("https://images.example.com/**", (route) => route.abort());
  await page.route(/\/community\/$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)}};</script><script>${answerSsoBridge}</script><img class="rounded-circle" src="https://images.example.com/avatar.png" width="48" height="48" alt="OpenClass Learner">`,
  }));

  await page.goto("/community/");

  const avatar = page.getByRole("img", { name: "OpenClass Learner" });
  await expect(avatar).toHaveAttribute("src", /^data:image\/png;base64,/);
  await expect(avatar).toHaveAttribute("data-openclass-avatar-fallback", "true");
  await expect(avatar).not.toHaveAttribute("data-src", /.+/);
});


test("syncs an Answer profile avatar through the same-origin OpenClass endpoint", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Playwright baseURL is required");
  const entryUrl = `${baseURL}/community`;
  const avatarBaseUrl = `${baseURL}/api/auth/community/avatar/`;
  await page.route(/\/community\/users\/user_answer$/, (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: `<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:${JSON.stringify(entryUrl)},avatarBaseUrl:${JSON.stringify(avatarBaseUrl)}};</script><script>${answerSsoBridge}</script><img class="rounded-circle" src="https://images.example.com/avatar.png" width="96" height="96" alt="OpenClass Learner">`,
  }));

  await page.goto("/community/users/user_answer");

  const avatar = page.getByRole("img", { name: "OpenClass Learner" });
  await expect(avatar).toHaveAttribute("src", `${avatarBaseUrl}user_answer`);
  await expect(avatar).toHaveAttribute("data-openclass-avatar-synced", "true");
  await expect(avatar).not.toHaveAttribute("data-openclass-avatar-fallback", /.+/);
});


test("reports an unavailable Answer service without exposing a second forum", async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Not logged in" }),
  }));
  await page.route("**/api/community/integration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      provider: "answer",
      public_url: "http://127.0.0.1:9080",
      entry_url: "http://127.0.0.1:9080",
      available: false,
      sso_enabled: false,
      setup_required: true,
    }),
  }));

  await page.goto("/community");

  await expect(page.getByRole("heading", { name: "Learning community is temporarily unavailable" })).toBeVisible();
  await expect(page.getByRole("button", { name: "recheck" })).toBeVisible();
  await expect(page.getByText("Keep using the built-in community")).toHaveCount(0);
});
