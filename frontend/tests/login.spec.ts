import { expect, test } from "@playwright/test";

import {
  mockGoogleLibrary,
  mockPublicConfigRequest,
  mockGoogleAuthRequest,
  mockAuthMeRequest,
  blockGoogleIdentityScript,
} from "./mocks";
import { buildURL, db, UserType } from "./playwright.config";
import { waitForLoginComplete } from "./shared";

const TEST_EMAIL = "test+login@example.com";

test.beforeAll(async () => {
  await db.removeUser(TEST_EMAIL);
});

test.afterAll(async () => {
  await db.removeUser(TEST_EMAIL);
  await db.close();
});

test.describe("Login page", () => {
  test("Website opens and redirects", async ({ page }) => {
    await page.goto(buildURL("/chat"));
    await expect(
      page.getByRole("heading", { name: "Sign-in to Max" }),
    ).toBeVisible();
  });

  test("Login - Account does not exist", async ({ page }) => {
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page.getByRole("textbox", { name: "Password" }).fill("test");
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(
      "Authentication failed",
    );
  });

  test("Registration - Passwords do not match", async ({ page }) => {
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("button", { name: "Don't have an account?" }).click();
    await page.getByRole("textbox", { name: "Full Name" }).fill("test");
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill("test");
    await page.getByRole("textbox", { name: "Confirm Password" }).fill("asdf");
    await page.getByRole("button", { name: "Create Account" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "Passwords do not match",
    );
  });

  test("Registration - Password too short", async ({ page }) => {
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("button", { name: "Don't have an account?" }).click();
    await page.getByRole("textbox", { name: "Full Name" }).fill("test");
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill("test");
    await page.getByRole("textbox", { name: "Confirm Password" }).fill("test");
    await page.getByRole("button", { name: "Create Account" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "Password must be at least 8 characters long",
    );
  });

  test("Registration - Success", async ({ page }) => {
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("button", { name: "Don't have an account?" }).click();
    await page.getByRole("textbox", { name: "Full Name" }).fill("test");
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill("testtest");
    await page
      .getByRole("textbox", { name: "Confirm Password" })
      .fill("testtest");
    await page.getByRole("button", { name: "Create Account" }).click();
    await expect(page.getByRole("heading")).toContainText(
      "Registration Successful!",
    );
  });

  test("Registration - Email already exists", async ({ page }) => {
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("button", { name: "Don't have an account?" }).click();
    await page.getByRole("textbox", { name: "Full Name" }).fill("test");
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill("testtest");
    await page
      .getByRole("textbox", { name: "Confirm Password" })
      .fill("testtest");
    await page.getByRole("button", { name: "Create Account" }).click();
    await expect(page.getByRole("alert")).toContainText(
      `User with email ${TEST_EMAIL} already exists`,
    );
  });

  test("Login - Inactive account", async ({ page }) => {
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page.getByRole("textbox", { name: "Password" }).fill("test");
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(
      "User account is inactive. Please contact an administrator for activation.",
    );
  });

  test("Login - Success and log out", async ({ page }) => {
    await db.activateUser(TEST_EMAIL);
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page.getByRole("textbox", { name: "Password" }).fill("testtest");
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await waitForLoginComplete(page);
    await page.locator(".user-menu").first().click();
    await page.getByRole("menuitem", { name: "Logout" }).click();
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
  });

  test("Login - Google sign in succeeds", async ({ page }) => {
    // Mock all the Google Signin calls
    await mockPublicConfigRequest(page);
    await blockGoogleIdentityScript(page);
    await mockGoogleLibrary(page);
    await mockGoogleAuthRequest(page, undefined);
    await mockAuthMeRequest(page);

    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("button", { name: "Sign in with Google" }).click();
    await expect(page.getByRole("paragraph")).toContainText(
      "Sign in with your Google account to continue",
    );
    await page.getByRole("button", { name: "Sign in with Google" }).click();

    await expect
      .poll(async () => {
        return await page.evaluate(() => localStorage.getItem("shu_token"));
      })
      .toBe("playwright-access-token");

    await expect
      .poll(async () => {
        return await page.evaluate(() =>
          localStorage.getItem("shu_refresh_token"),
        );
      })
      .toBe("playwright-refresh-token");

    await waitForLoginComplete(page);
  });

  test("Login - Using password with existing Google account", async ({
    page,
  }) => {
    await db.removeUser(TEST_EMAIL);
    await db.createUser(TEST_EMAIL, UserType.Google);
    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("textbox", { name: "Email Address" }).fill(TEST_EMAIL);
    await page.getByRole("textbox", { name: "Password" }).fill("testtest");
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(
      "The provided account was created using Google. Please use the Google login flow.",
    );
  });

  test("Login - Using Google with existing password account", async ({
    page,
  }) => {
    // Mock all the Google Signin calls
    await mockPublicConfigRequest(page);
    await blockGoogleIdentityScript(page);
    await mockGoogleLibrary(page);
    await mockGoogleAuthRequest(page, {
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "HTTP_409",
          message:
            "The provided account was created using a password. Please use the username & password login flow.",
          details: {},
        },
      }),
    });
    await mockAuthMeRequest(page);

    await page.goto(buildURL("/"));
    await expect(page.locator("#root")).toContainText(
      "Sign in to your account",
    );
    await page.getByRole("button", { name: "Sign in with Google" }).click();
    await expect(page.getByRole("paragraph")).toContainText(
      "Sign in with your Google account to continue",
    );
    await page.getByRole("button", { name: "Sign in with Google" }).click();

    await expect(page.getByRole("alert")).toContainText(
      "The provided account was created using a password. Please use the username & password login flow.",
    );
  });
});
