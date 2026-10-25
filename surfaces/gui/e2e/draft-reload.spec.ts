import { expect } from '@playwright/test';
import { test } from './fixtures';
test('unsent draft survives reload', async ({page}) => {
 await page.goto('/'); await page.getByTitle('Draft the launch note').first().click();
 const box=page.getByPlaceholder(/Ask the coworker/);await box.fill('draft before reload');
 await page.reload(); await page.getByTitle('Draft the launch note').first().click();
 await expect(box).toHaveValue('draft before reload');
});
