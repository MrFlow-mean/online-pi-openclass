import { expect, test } from "@playwright/test";

const adminUser = {
  id: "user_logout_admin",
  email: "logout-admin@example.com",
  phone: null,
  role: "admin",
  display_name: "Logout Admin",
  avatar_url: null,
  created_at: "2026-07-30T00:00:00+00:00",
  last_login_at: null,
  auth_identities: [],
};

async function mockAuthenticatedSession(page: import("@playwright/test").Page) {
  await page.context().addCookies([
    {
      name: "openclass.auth.token",
      value: "formal-session-token",
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
    },
  ]);
  await page.route("**/api/auth/me", (route) => {
    const authenticated = route.request().headers()["cookie"]?.includes("openclass.auth.token=formal-session-token");
    return route.fulfill({
      status: authenticated ? 200 : 401,
      contentType: "application/json",
      body: JSON.stringify(authenticated ? adminUser : { detail: "未登录" }),
    });
  });
}

test("the account menu revokes the backend session before leaving", async ({ page }) => {
  await mockAuthenticatedSession(page);
  await page.route("**/api/admin/overview", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        stats: { users: 1, admins: 1, packages: 0, lessons: 0, resources: 0 },
        users: [adminUser],
      }),
    })
  );

  let logoutRequests = 0;
  await page.route("**/api/auth/logout", async (route) => {
    logoutRequests += 1;
    expect(route.request().method()).toBe("POST");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "set-cookie": "openclass.auth.token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" },
      body: JSON.stringify({ message: "已退出登录" }),
    });
  });

  await page.goto("/admin");
  await page.getByRole("button", { name: "Logout Admin" }).click();
  await page.getByRole("menuitem", { name: "退出登录" }).click();

  await expect.poll(() => logoutRequests).toBe(1);
  await expect(page).toHaveURL(/\/login$/);
});
