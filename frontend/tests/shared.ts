import { Page, expect } from "@playwright/test";

export const waitForLoginComplete = async (page: Page) => {
  await expect(page.locator('h4')).toContainText('Welcome to Max');
};
