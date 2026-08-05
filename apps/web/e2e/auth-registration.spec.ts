import { expect, test } from "@playwright/test";

test("email registration requires username, repeated password, and a verification code", async ({ page }) => {
  test.skip(Boolean(process.env.NEXT_PUBLIC_CLOUDFLARE_TURNSTILE_SITE_KEY), "runs without production Turnstile");

  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Not logged in" }),
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
        message: "Verification code has been sent, please check your email",
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
          display_name: "learner",
          avatar_url: null,
          created_at: "2026-07-27T00:00:00+00:00",
          last_login_at: null,
          auth_identities: [],
        },
      }),
    });
  });

  await page.goto("/login");
  await page.locator('a[href="/register"]').first().click();
  await expect(page).toHaveURL(/\/register/);

  await page.getByLabel("Email", { exact: true }).fill("student@example.com");
  await page.getByLabel("Username").fill("learner");
  await page.getByLabel("Password", { exact: true }).fill("correct-password");
  await page.getByLabel("Confirm Password").fill("correct-password");
  const sendCodeButton = page.getByRole("button", { name: "Send verification code" });
  await expect(sendCodeButton).toBeEnabled();
  await sendCodeButton.click();

  await expect.poll(() => codeRequestBody).toEqual({ email: "student@example.com" });
  await expect(page.getByText("Verification code has been sent, please check your email")).toBeVisible();
  await page.getByLabel("Email verification code").fill("123456");
  await page.getByRole("button", { name: "Create account", exact: true }).click();

  await expect.poll(() => registrationRequestBody).toEqual({
    email: "student@example.com",
    username: "learner",
    password: "correct-password",
    password_confirmation: "correct-password",
    challenge_id: "email_challenge_registration",
    code: "123456",
    guest_token: null,
  });
});

test("registration code action stays usable when Turnstile needs attention", async ({ page }) => {
  test.skip(!process.env.NEXT_PUBLIC_CLOUDFLARE_TURNSTILE_SITE_KEY, "requires a production-style Turnstile build");

  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Not logged in" }),
    })
  );
  await page.route("**/api/auth/providers", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    })
  );
  await page.route("https://challenges.cloudflare.com/turnstile/v0/api.js**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: `window.turnstile = {
        render: (_container, options) => {
          setTimeout(() => options["error-callback"](), 0);
          return "turnstile-test-widget";
        },
        remove: () => {},
        reset: () => {},
      };`,
    })
  );

  let codeRequestCount = 0;
  await page.route("**/api/auth/register/email/code", (route) => {
    codeRequestCount += 1;
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Captcha request should not be sent before verification is complete" }),
    });
  });

  await page.goto("/register");
  await expect(page.getByLabel("Cloudflare Turnstile human verification")).toBeVisible();

  const sendCodeButton = page.getByRole("button", { name: "Send verification code" });
  await expect(sendCodeButton).toBeEnabled();
  await sendCodeButton.click();

  await expect(page.getByText("Complete human verification before requesting an email code.")).toBeVisible();
  expect(codeRequestCount).toBe(0);
});
