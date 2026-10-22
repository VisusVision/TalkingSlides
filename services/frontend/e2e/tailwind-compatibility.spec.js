import { expect, test } from '@playwright/test';

async function openStyleProbe(page, theme = 'light') {
  await page.addInitScript((theme) => {
    localStorage.clear(); sessionStorage.clear(); localStorage.setItem('visus-theme-mode', theme);
  }, theme);
  await page.route('**/api/v1/**', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ results: [], features: {}, sections: [] }) }));
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await page.evaluate(() => {
    const probe = document.createElement('section'); probe.id = 'style-probe';
    probe.style.cssText = 'position:fixed;inset:100px auto auto 100px;z-index:9999';
    probe.innerHTML = '<button id="motion-probe" class="focus-ring motion-interactive h-control-md rounded-lg enabled:active:scale-[0.98] bg-[var(--surface)] text-[var(--text-primary)]" style="width:200px">Style probe</button><div id="token-probe" class="rounded-token-lg font-display leading-tight tracking-tight">Türkçe &amp; English</div>';
    document.body.append(probe);
  });
}

test('manual theme overrides the OS and stock utilities stay separate from product tokens', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await openStyleProbe(page);
  await expect(page.locator('html')).not.toHaveClass(/dark/);
  await expect(page.locator('#motion-probe')).toHaveCSS('border-radius', '8px');
  await expect(page.locator('#motion-probe')).toHaveCSS('height', '44px');
  await expect(page.locator('#motion-probe')).toHaveCSS('background-color', 'rgb(249, 249, 255)');
  await expect(page.locator('#token-probe')).toHaveCSS('border-radius', '12px');
  await expect(page.locator('#token-probe')).toHaveCSS('line-height', '20px');
  await expect(page.locator('#token-probe')).toHaveCSS('letter-spacing', '-0.4px');
  await expect(page.locator('#token-probe')).toHaveCSS('font-family', /Manrope/);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.evaluate(() => localStorage.setItem('visus-theme-mode', 'dark'));
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(17, 19, 23)');
});

test('keyboard focus retains its visible ring and forced-colors outline', async ({ page }) => {
  await openStyleProbe(page);
  await page.keyboard.press('Tab');
  await page.locator('#motion-probe').focus();
  await expect(page.locator('#motion-probe')).toHaveCSS('outline-style', 'solid');
  await expect(page.locator('#motion-probe')).toHaveCSS('outline-width', '2px');
  expect(await page.locator('#motion-probe').evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
  await page.emulateMedia({ forcedColors: 'active' });
  await expect(page.locator('#motion-probe')).toHaveCSS('outline-style', 'solid');
  await expect(page.locator('#motion-probe')).toHaveCSS('outline-width', '2px');
});

for (const preference of ['system', 'class']) test(`active transforms respect ${preference} reduced motion`, async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await openStyleProbe(page);
  const button = page.locator('#motion-probe');
  await button.hover(); await page.mouse.down();
  await expect.poll(async () => (await button.boundingBox()).width).toBeCloseTo(196, 0);
  await page.mouse.up();
  if (preference === 'system') await page.emulateMedia({ reducedMotion: 'reduce' });
  else await page.evaluate(() => document.documentElement.classList.add('reduced-motion'));
  await button.hover(); await page.mouse.down();
  await expect.poll(async () => (await button.boundingBox()).width).toBe(200);
  await page.mouse.up();
});
