import { expect, test } from "@playwright/test";


const readyIntegration = {
  provider: "answer",
  public_url: "https://community.example.com",
  entry_url: "https://community.example.com/answer/api/v1/connector/login/basic",
  available: true,
  sso_enabled: true,
  setup_required: false,
};


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
      display_name: "学习者甲",
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


test("sends an anonymous reader to the public Answer site", async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: "未登录" }),
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


test("reports an unavailable Answer service without exposing a second forum", async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: "未登录" }),
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

  await expect(page.getByRole("heading", { name: "学习社区暂时不可用" })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新检查" })).toBeVisible();
  await expect(page.getByText("继续使用内置社区")).toHaveCount(0);
});
