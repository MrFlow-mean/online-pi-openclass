import { expect, test } from "@playwright/test";

const guestUser = {
  id: "guest-route-policy",
  email: "guest-route-policy@guest.openclass.local",
  role: "guest",
  display_name: "游客",
  avatar_url: null,
  created_at: "2026-07-29T00:00:00+00:00",
  last_login_at: null,
  auth_identities: [],
};

test.beforeEach(async ({ page }) => {
  await page.context().addCookies([
    {
      name: "openclass.guest.auth.token",
      value: "guest-route-token",
      domain: "127.0.0.1",
      path: "/",
    },
  ]);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(guestUser),
    })
  );
});

for (const restrictedPath of [
  "/",
  "/home",
  "/community",
  "/trending",
  "/profile",
  "/following",
  "/contributions",
  "/wallet",
]) {
  test(`keeps a guest out of ${restrictedPath}`, async ({ page }) => {
    await page.goto(restrictedPath);
    await expect(page).toHaveURL(/\/studio$/);
  });
}

test("allows a guest to use Studio", async ({ page }) => {
  await page.goto("/studio");
  await expect(page).toHaveURL(/\/studio$/);
});
