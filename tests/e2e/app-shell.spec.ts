import { expect, test } from "@playwright/test";

test.describe("application shell", () => {
  test("loads the product identity from the production build", async ({ page }) => {
    const response = await page.goto("/");

    expect(response?.status()).toBe(200);
    await expect(page).toHaveTitle("Industry Intelligence Platform");

    const main = page.getByRole("main");

    await expect(main).toBeVisible();
    await expect(
      main.getByRole("heading", {
        level: 1,
        name: "Industry Intelligence Platform",
      }),
    ).toBeVisible();
  });
});
