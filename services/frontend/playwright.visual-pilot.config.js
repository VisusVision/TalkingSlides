import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-pilot',
  timeout: 90_000,
  workers: 2,
  reporter: [['list'], ['html', { outputFolder: 'visual-pilot-report', open: 'never' }]],
  outputDir: 'visual-pilot-results',
  snapshotPathTemplate: '{testDir}/.snapshots/{testName}/{arg}{ext}',
  use: { trace: 'retain-on-failure' },
  webServer: [
    { command: 'npm run preview -- --host 127.0.0.1 --port 4173', url: 'http://127.0.0.1:4173', reuseExistingServer: false },
    { command: 'npm run preview -- --host 127.0.0.1 --port 4174', cwd: process.env.TAILWIND_BASELINE_FRONTEND, url: 'http://127.0.0.1:4174', reuseExistingServer: false },
  ],
});
