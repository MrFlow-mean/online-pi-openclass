import { expect, test } from "@playwright/test";

test("shows PayPal points at face value without exposing the internal cost value", async ({ context, page }) => {
  await context.addCookies([
    {
      name: "openclass.auth.token",
      value: "wallet-test-token",
      domain: "127.0.0.1",
      path: "/",
    },
  ]);
  await context.addInitScript(() => {
    window.localStorage.setItem("openclass.auth.token", "wallet-test-token");
  });
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "user-test",
        email: "wallet@example.com",
        role: "user",
        display_name: "Wallet Test",
        avatar_url: null,
        created_at: "2026-07-26T00:00:00Z",
        last_login_at: "2026-07-26T00:00:00Z",
        auth_identities: [],
      }),
    })
  );
  await page.route("**/api/billing/wallet", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        wallet: {
          user_id: "user-test",
          balance_credits: 10_000,
          reserved_credits: 0,
          available_credits: 10_000,
          paypal_configured: true,
          currency: "USD",
          updated_at: "2026-07-26T00:00:00Z",
        },
        packages: [
          {
            id: "usd_10000",
            amount_cents: 10_000,
            amount_usd: "100.00",
            credits: 10_000,
          },
        ],
      }),
    })
  );
  await page.route("**/api/billing/transactions", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );

  await page.goto("/wallet");

  await expect(page.getByText("获得 10,000 点数", { exact: true })).toBeVisible();
  await expect(page.getByText("可用积分", { exact: true })).toHaveCount(0);
  await expect(page.getByText("充值比例", { exact: true })).toHaveCount(0);
  await expect(page.getByText("收款状态", { exact: true })).toHaveCount(0);
  await expect(page.getByText("$75", { exact: false })).toHaveCount(0);
  await expect(page.getByText("75 美元", { exact: false })).toHaveCount(0);
});
