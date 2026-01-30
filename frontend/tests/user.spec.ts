import { expect, test } from "@playwright/test";

import { buildURL, db, UserType } from "./playwright.config";
import { waitForLoginComplete } from "./shared";

const TEST_EMAIL = "test+user@example.com";

test.beforeAll(async () => {
  await db.removeUser(TEST_EMAIL);
  await db.createUser(TEST_EMAIL, UserType.Password);
});

test.afterAll(async () => {
  await db.removeUser(TEST_EMAIL);
  await db.close();
});

test.describe("User pages", () => {
  const login = async (page) => {
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page.getByRole("textbox", { name: "Password" }).fill("password");
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await waitForLoginComplete(page);
  };

  test("Chat", async ({ page }) => {
    const modelName = await db.getRandomModel();
    console.log(`Testing using model: ${modelName}`);

    await login(page);

    await page.getByRole("button", { name: "New Chat", exact: true }).click();
    await page.getByRole("combobox").click();
    await page.locator("#menu-").getByText(modelName, { exact: true }).click();

    await page
      .getByRole("textbox", { name: "Type your message..." })
      .fill("Give me around 200 words of lorem ipsum");
    await page.getByRole("button", { name: "Send" }).click();

    const messageTarget = page.locator('[id^="msg-streaming-"]');

    // make sure we show the "Thinking..."" bubble.
    await expect
      .poll(async () => {
        return await messageTarget.last().textContent();
      })
      .toContain("Thinking…");

    // Make sure we load anything else from the backend.
    await expect
      .poll(
        async () => {
          return await messageTarget.last().textContent();
        },
        { timeout: 60_000 },
      )
      .not.toContain("Thinking…");

    // If data is streaming in, the text int he bubble should incrementally change.
    const prev = (await messageTarget.last().textContent()) ?? "";
    await expect
      .poll(async () => (await messageTarget.last().textContent()) ?? "", {
        timeout: 10_000,
      })
      .not.toEqual(prev);

    // Delete the conversation
    await page.getByRole("button").nth(4).click(); // these buttons need classes attached so we can target them correctly
    await page.getByRole("button", { name: "Delete" }).click();
  });
});
