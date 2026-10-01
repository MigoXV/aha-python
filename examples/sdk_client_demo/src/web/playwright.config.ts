import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8091",
    viewport: { width: 1280, height: 1000 },
    permissions: ["microphone"],
    launchOptions: {
      args: [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
      ],
    },
  },
  webServer: {
    command: "cd ../.. && poetry run python tests/browser_server.py",
    url: "http://127.0.0.1:8091/api/config",
    reuseExistingServer: false,
    timeout: 15000,
  },
});
