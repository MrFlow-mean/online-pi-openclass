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

test("shows PayPal, eligible cards, Apple Pay, and Google Pay for the selected package", async ({
  context,
  page,
}) => {
  await context.addCookies([
    {
      name: "openclass.auth.token",
      value: "wallet-methods-token",
      domain: "127.0.0.1",
      path: "/",
    },
  ]);
  await page.addInitScript(() => {
    window.localStorage.setItem("openclass.auth.token", "wallet-methods-token");
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
          balance_credits: 0,
          reserved_credits: 0,
          available_credits: 0,
          paypal_configured: true,
          currency: "USD",
          updated_at: "2026-07-26T00:00:00Z",
        },
        packages: [{ id: "usd_10000", amount_cents: 10_000, amount_usd: "100.00", credits: 10_000 }],
      }),
    })
  );
  await page.route("**/api/billing/transactions", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );
  await page.route("**/api/billing/paypal/client-config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        client_id: "client-id",
        client_token: "client-token",
        currency: "USD",
        mode: "sandbox",
      }),
    })
  );
  await page.route("https://www.paypal.com/sdk/js?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: `
        window.paypal = {
          Buttons: () => ({
            isEligible: () => true,
            render: async (target) => {
              const button = document.createElement("button");
              button.textContent = "PayPal";
              document.querySelector(target).appendChild(button);
            },
          }),
          CardFields: () => ({
            isEligible: () => true,
            NameField: () => ({ render: async () => {} }),
            NumberField: () => ({ render: async () => {} }),
            ExpiryField: () => ({ render: async () => {} }),
            CVVField: () => ({ render: async () => {} }),
            submit: async () => {},
          }),
          Applepay: () => ({
            config: async () => ({
              isEligible: true,
              countryCode: "US",
              merchantCapabilities: ["supports3DS"],
              supportedNetworks: ["visa", "masterCard"],
            }),
          }),
          Googlepay: () => ({
            config: async () => ({ allowedPaymentMethods: [{ type: "CARD" }], merchantInfo: {} }),
          }),
        };
      `,
    })
  );
  await page.route("https://applepay.cdn-apple.com/jsapi/1.latest/apple-pay-sdk.js", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: `
        window.ApplePaySession = class ApplePaySession {
          static STATUS_SUCCESS = 1;
          static STATUS_FAILURE = 2;
          static canMakePayments() { return true; }
        };
      `,
    })
  );
  await page.route("https://pay.google.com/gp/p/js/pay.js", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: `
        window.google = { payments: { api: { PaymentsClient: class PaymentsClient {
          async isReadyToPay() { return { result: true }; }
          createButton() {
            const button = document.createElement("button");
            button.textContent = "Google Pay";
            return button;
          }
          async loadPaymentData() {}
        } } } };
      `,
    })
  );

  await page.goto("/wallet");
  await page.getByRole("button", { name: /\$100\.00/ }).click();

  await expect(page.getByRole("button", { name: "PayPal", exact: true })).toBeVisible();
  await expect(page.getByTestId("paypal-card-fields")).toBeVisible();
  await expect(page.locator("apple-pay-button")).toBeVisible();
  await expect(page.getByRole("button", { name: "Google Pay", exact: true })).toBeVisible();
});
