import { defineConfig, devices } from '@playwright/test';

const devServerPort = process.env.PLAYWRIGHT_PORT || '3000';
const devServerHost = process.env.PLAYWRIGHT_HOST || '127.0.0.1';
const devServerUrl = `http://${devServerHost}:${devServerPort}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: devServerUrl,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `npm run dev -- --host ${devServerHost} --port ${devServerPort}`,
    url: devServerUrl,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
