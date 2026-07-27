import { expect, test } from "@playwright/test";

test("email registration requires username, repeated password, and a verification code", async ({ page }) => {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "未登录" }),
    })
  );
  await page.route("**/api/auth/providers", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    })
  );

  let codeRequestBody: Record<string, unknown> | null = null;
  await page.route("**/api/auth/register/email/code", async (route) => {
    codeRequestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        challenge_id: "email_challenge_registration",
        expires_in_seconds: 600,
        message: "验证码已发送，请检查邮箱",
      }),
    });
  });

  let registrationRequestBody: Record<string, unknown> | null = null;
  await page.route("**/api/auth/register", async (route) => {
    registrationRequestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        token: "registered-user-token",
        user: {
          id: "user_registered",
          email: "student@example.com",
          phone: null,
          role: "user",
          display_name: "学习者",
          avatar_url: null,
          created_at: "2026-07-27T00:00:00+00:00",
          last_login_at: null,
          auth_identities: [],
        },
      }),
    });
  });

  await page.goto("/login");
  await page.getByRole("link", { name: "邮箱注册" }).click();
  await expect(page).toHaveURL(/\/register/);

  await page.getByLabel("邮箱", { exact: true }).fill("student@example.com");
  await page.getByLabel("用户名").fill("学习者");
  await page.getByLabel("密码", { exact: true }).fill("correct-password");
  await page.getByLabel("确认密码").fill("correct-password");
  await page.getByRole("button", { name: "发送验证码" }).click();

  await expect.poll(() => codeRequestBody).toEqual({ email: "student@example.com" });
  await expect(page.getByText("验证码已发送，请检查邮箱")).toBeVisible();
  await page.getByLabel("邮箱验证码").fill("123456");
  await page.getByRole("button", { name: "注册", exact: true }).click();

  await expect.poll(() => registrationRequestBody).toEqual({
    email: "student@example.com",
    username: "学习者",
    password: "correct-password",
    password_confirmation: "correct-password",
    challenge_id: "email_challenge_registration",
    code: "123456",
    guest_token: null,
  });
});
