import { expect, test, type Page } from "@playwright/test";

const CJK_TEXT = /[\u3400-\u9fff]/;
const API_BASE_URL = `http://127.0.0.1:${process.env.OPENCLASS_E2E_API_PORT ?? "8110"}`;

async function interfaceText(page: Page) {
  return page.evaluate(() => {
    const attributeText = Array.from(document.querySelectorAll<HTMLElement>("[aria-label], [placeholder], [title]"))
      .flatMap((element) => [
        element.getAttribute("aria-label"),
        element.getAttribute("placeholder"),
        element.getAttribute("title"),
      ])
      .filter(Boolean)
      .join("\n");
    return `${document.body.innerText}\n${attributeText}`;
  });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "openclass.profile.settings",
      JSON.stringify({ interfaceLanguage: "zh-CN" })
    );
  });
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not signed in" }) })
  );
  await page.route("**/api/auth/providers", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );
});

for (const path of [
  "/login",
  "/register",
  "/forgot-password",
  "/reset-password",
  "/privacy",
  "/terms",
  "/security",
  "/tech-docs",
  "/courses/concept-explainer",
]) {
  test(`renders ${path} in English`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect.poll(async () => CJK_TEXT.test(await interfaceText(page))).toBe(false);
  });
}

test("renders Studio chrome in English", async ({ page }) => {
  await page.unroute("**/api/auth/me");
  const guestResponse = await page.request.post(`${API_BASE_URL}/api/auth/guest`);
  expect(guestResponse.ok()).toBe(true);
  const { token } = (await guestResponse.json()) as { token: string };
  const packageResponse = await page.request.post(`${API_BASE_URL}/api/packages`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { title: "English interface test", summary: "" },
  });
  expect(packageResponse.ok()).toBe(true);

  await page.goto("/login");
  await page.evaluate((guestToken) => {
    window.sessionStorage.setItem("openclass.guest.auth.token", guestToken);
    window.localStorage.setItem("openclass.connected-guest.auth.token", guestToken);
    document.cookie = `openclass.guest.auth.token=${encodeURIComponent(guestToken)}; Path=/; SameSite=Lax`;
  }, token);
  await page.goto("/studio");

  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "This package is empty" })).toBeVisible();
  await expect.poll(async () => CJK_TEXT.test(await interfaceText(page))).toBe(false);
});
