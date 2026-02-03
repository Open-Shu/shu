import { Page } from "@playwright/test";

const GOOGLE_TEST_USER_EMAIL = "playwright.user@example.com";
const GOOGLE_TEST_CREDENTIAL = "fake-google-jwt";

export const mockGoogleLibrary = async (page: Page) => {
  await page.addInitScript(
    ({ credential }) => {
      const win = window as any;
      win.google = {
        accounts: {
          id: {
            initialize({ callback }) {
              win.__playwrightGoogleCallback = callback;
            },
            prompt() {
              setTimeout(() => {
                win.__playwrightGoogleCallback?.({ credential });
              }, 0);
            },
          },
        },
      };
    },
    { credential: GOOGLE_TEST_CREDENTIAL },
  );
};

export const mockPublicConfigRequest = async (page: Page) => {
  await page.route("**/api/v1/config/public", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          app_name: "Max",
          google_client_id: "playwright-client-id",
          version: "test",
        },
      }),
    });
  });
};

export const mockGoogleAuthRequest = async (
  page: Page,
  result: {} | undefined,
) => {
  if (result === undefined) {
    result = {
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          access_token: "playwright-access-token",
          refresh_token: "playwright-refresh-token",
          user: {
            email: GOOGLE_TEST_USER_EMAIL,
            name: "Playwright User",
            role: "regular_user",
          },
        },
      }),
    };
  }
  await page.route("**/auth/login", async (route) => {
    await route.fulfill(result);
  });
};

export const mockAuthMeRequest = async (page: Page) => {
  await page.route("**/auth/me", async (route) => {
    const body = JSON.stringify({
      data: {
        user_id: "playwright-user-id",
        email: GOOGLE_TEST_USER_EMAIL,
        name: "Playwright User",
        role: "regular_user",
        is_active: true,
      },
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body,
    });
  });
};

export const blockGoogleIdentityScript = async (page: Page) => {
  await page.route("https://accounts.google.com/gsi/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: "// Playwright stubbed Google identity script",
    });
  });
};
