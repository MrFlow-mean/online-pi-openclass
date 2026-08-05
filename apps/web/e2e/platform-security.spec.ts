import { expect, test } from "@playwright/test";

test("legal and security routes remain public and publish crawler metadata", async ({ page, request }) => {
  for (const [path, heading] of [
    ["/privacy", "privacy policy"],
    ["/terms", "Terms of Service"],
    ["/security", "Safety instructions"],
  ] as const) {
    await page.goto(path);
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }

  const robots = await request.get("/robots.txt");
  expect(robots.ok()).toBeTruthy();
  expect(await robots.text()).toContain("Sitemap: https://open-classes.com/sitemap.xml");

  const sitemap = await request.get("/sitemap.xml");
  expect(sitemap.ok()).toBeTruthy();
  expect(await sitemap.text()).toContain("/privacy");
  expect(await sitemap.text()).toContain("/security");
});

test("production web responses carry the platform security policy", async ({ page }) => {
  const response = await page.goto("/login");
  expect(response).not.toBeNull();
  const headers = response!.headers();

  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["strict-transport-security"]).toContain("max-age=31536000");
  expect(headers["content-security-policy"]).toContain("https://challenges.cloudflare.com");
  expect(headers["content-security-policy"]).toContain("https://images.example.com");
  expect(headers["content-security-policy"]).not.toContain("http://insecure.example.com");
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
});
