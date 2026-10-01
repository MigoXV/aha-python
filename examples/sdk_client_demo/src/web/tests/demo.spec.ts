import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function selectChoice(page: Page, label: string, option: string) {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

test("microphone produces interim text before stopping and final text after flushing", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("status", { name: "上游访问状态" })).toHaveText(
    "上游访问成功 · 3 个已发布模型",
  );
  await page.getByRole("combobox", { name: "Model slug" }).click();
  await expect(page.getByRole("option")).toHaveCount(3);
  await page.getByRole("combobox", { name: "Model slug" }).fill("test-workflow");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始录音" }).click();
  await expect(page.getByText("正在采集麦克风", { exact: true })).toBeVisible();
  await expect(page.getByText(/\d+\.\d s 已发送/)).not.toHaveText(
    "0.0 s 已发送",
  );
  await expect(page.getByText("录音期间收到的临时转写。", { exact: true })).toBeVisible();
  await expect(page.getByText("临时转写", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "停止录音" })).toBeVisible();
  await page.getByRole("button", { name: "停止录音" }).click();
  await expect(
    page.getByText("麦克风音频已通过 SDK 提交。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("录音期间收到的临时转写。", { exact: true })).toHaveCount(0);
  await page
    .getByRole("button", {
      name: /conversation.item.input_audio_transcription.completed/,
    })
    .click();
  await expect(page.locator(".event-detail pre")).toContainText("麦克风音频");
  await page.getByRole("button", { name: "断开连接" }).click();
  await expect(page.getByText("未连接", { exact: true })).toBeVisible();
});

test("upstream is checked on entry and a failed check can be retried", async ({
  page,
}) => {
  let fail = true;
  await page.route("**/api/models", async (route) => {
    if (fail) {
      await route.fulfill({
        status: 502,
        json: { detail: "上游返回 HTTP 401；请检查服务地址和访问凭据" },
      });
    } else {
      await route.continue();
    }
  });
  await page.goto("/");
  const upstream = page.getByRole("status", { name: "上游访问状态" });
  await expect(upstream).toHaveText("上游访问失败");
  await expect(page.locator("#model-help")).toContainText("HTTP 401");
  await expect(page.getByText("未连接", { exact: true })).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "重试访问" }).click();
  await expect(upstream).toHaveText("上游访问成功 · 3 个已发布模型");
  await expect(page.locator("#model-help")).not.toContainText("HTTP 401");
  await page.getByRole("combobox", { name: "Model slug" }).click();
  await expect(page.getByRole("option")).toHaveCount(3);
});

test("an accessible upstream with no published models is shown as empty", async ({
  page,
}) => {
  await page.route("**/api/models", (route) =>
    route.fulfill({ json: { models: [] } }),
  );
  await page.goto("/");
  await expect(page.getByRole("status", { name: "上游访问状态" })).toHaveText(
    "上游访问成功 · 0 个已发布模型",
  );
  await expect(page.locator("#model-help")).toContainText("尚无已发布模型");
});

test("custom choices support filtering, manual models and keyboard selection", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("status", { name: "上游访问状态" })).toContainText("上游访问成功");
  await expect(page.locator("select, datalist")).toHaveCount(0);
  const model = page.getByRole("combobox", { name: "Model slug" });
  await model.fill("workflow");
  await expect(page.getByRole("option")).toHaveCount(1);
  await model.press("ArrowDown");
  await model.press("Enter");
  await expect(model).toHaveValue("test-workflow");
  await expect(model).toHaveAttribute("aria-expanded", "false");
  await model.fill("custom-model");
  await expect(page.getByText("没有匹配模型，可直接使用输入的 slug")).toBeVisible();
  await model.press("Escape");
  await expect(model).toHaveValue("custom-model");
  const mode = page.getByRole("combobox", { name: "交互模式" });
  await mode.focus();
  await mode.press("ArrowDown");
  await mode.press("End");
  await mode.press("Enter");
  await expect(mode).toHaveText("语音 Agent");
  await expect(mode).toBeFocused();
  const rate = page.getByRole("combobox", { name: "输入采样率" });
  await rate.focus();
  await rate.press(" ");
  await rate.press("ArrowDown");
  await rate.press("Enter");
  await expect(rate).toHaveText("24 kHz");
  await rate.click();
  await rate.press("Escape");
  await expect(rate).toHaveAttribute("aria-expanded", "false");
  await rate.click();
  await rate.press("Tab");
  await expect(rate).toHaveAttribute("aria-expanded", "false");
});

for (const theme of ["vallum", "abyssus"]) {
  test(`${theme}: open choices fit the viewport and remain accessible`, async ({ page }, testInfo) => {
    await page.goto("/");
    await expect(page.getByRole("status", { name: "上游访问状态" })).toContainText("上游访问成功");
    if (theme === "abyssus")
      await page.getByRole("button", { name: "切换苍渊主题" }).click();
    for (const width of [1280, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const label of ["Model slug", "交互模式", "输入采样率"]) {
        const control = page.getByRole("combobox", { name: label, exact: true });
        await control.click();
        await expect(control).toHaveAttribute("aria-expanded", "true");
        const popup = await page.getByRole("listbox").boundingBox();
        expect(popup).not.toBeNull();
        expect(popup!.x).toBeGreaterThanOrEqual(0);
        expect(popup!.x + popup!.width).toBeLessThanOrEqual(width);
        expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations).toEqual([]);
        await page.screenshot({ path: testInfo.outputPath(`${theme}-${width}-${label}.png`), fullPage: true });
        await control.press("Escape");
        await expect(control).toBeFocused();
      }
    }
  });
}

test("agent text, audio, explicit approval, cancellation and clear", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Model slug" }).fill("approval-agent");
  await selectChoice(page, "交互模式", "语音 Agent");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByLabel("文本消息").fill("你好，请测试客户端。");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("工具需要你的审批")).toBeVisible();
  await expect(
    page.getByText("你好，SDK 客户端连接正常。", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "拒绝", exact: true }).click();
  await expect(
    page.getByText("你好，SDK 客户端连接正常。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("工具需要你的审批")).toHaveCount(0);
  await expect(page.getByText("响应 completed", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消响应" }).click();
  await expect(page.getByText("响应 cancelled", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "清空正文" }).click();
  await expect(page.getByText("让声音成为内容")).toBeVisible();
});

test("failed handshake explains the error and supports reconnect", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Model slug" }).fill("reject");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByRole("alert")).toContainText("模型未发布");
  await expect(page.getByText("连接中断", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Model slug" }).fill("test-workflow");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("microphone tracks are released when disconnecting during capture", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(
      navigator.mediaDevices,
    );
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await original(constraints);
      (window as typeof window & { testStream: MediaStream }).testStream =
        stream;
      return stream;
    };
  });
  await page.goto("/");
  await page.getByRole("combobox", { name: "Model slug" }).fill("test-workflow");
  await selectChoice(page, "输入采样率", "24 kHz");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始录音" }).click();
  await expect(page.getByText("正在采集麦克风", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "断开连接" }).click();
  await expect(page.getByText("未连接", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() =>
      (window as typeof window & { testStream: MediaStream }).testStream
        .getTracks()
        .every((track) => track.readyState === "ended"),
    ),
  ).toBe(true);
});

test("denied microphone permission leaves the session usable", async ({
  page,
}) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException("Permission denied", "NotAllowedError");
    };
  });
  await page.goto("/");
  await page.getByRole("combobox", { name: "Model slug" }).fill("test-workflow");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始录音" }).click();
  await expect(page.getByRole("alert")).toContainText("检查浏览器权限后重试");
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始录音" })).toBeEnabled();
});

test("agent questions use an explicit answer and remain accessible", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Model slug" }).fill("question-agent");
  await selectChoice(page, "交互模式", "语音 Agent");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByLabel("文本消息").fill("请发起问题");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("Agent 需要补充信息")).toBeVisible();
  await page.setViewportSize({ width: 320, height: 1000 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await page
    .getByLabel("回答 JSON 数组")
    .fill('[{"id":"q","selected":[],"custom":"是"}]');
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.getByText("Agent 需要补充信息")).toHaveCount(0);
  await expect(page.getByText("响应 completed", { exact: true })).toBeVisible();
});

for (const theme of ["vallum", "abyssus"]) {
  test(`${theme}: keyboard, contrast, reduced motion and responsive layout`, async ({
    page,
  }, testInfo) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await page.keyboard.press("Tab");
    await expect(
      page.getByRole("link", { name: "跳到会话内容" }),
    ).toBeFocused();
    await page.keyboard.press("Tab");
    if (theme === "abyssus")
      await page.getByRole("button", { name: "切换苍渊主题" }).click();
    for (const width of [1280, 768, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const audit = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
        .analyze();
      expect(
        audit.violations.map(
          (value) =>
            `${value.id}: ${value.nodes.map((node) => node.target.join(",")).join(";")}`,
        ),
      ).toEqual([]);
      await page.screenshot({
        path: testInfo.outputPath(`${theme}-${width}.png`),
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 640, height: 800 });
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "28px";
      document.body.style.zoom = "2";
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test("MCP conversation item requires a deliberate approval through the SDK", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Model slug" }).fill("mcp-agent");
  await selectChoice(page, "交互模式", "语音 Agent");
  await page.getByRole("button", { name: "连接会话" }).click();
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByLabel("文本消息").fill("测试 MCP");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("工具需要你的审批")).toBeVisible();
  await page.getByRole("button", { name: "允许", exact: true }).click();
  await expect(page.getByText("工具需要你的审批")).toHaveCount(0);
  await expect(page.getByText("响应 completed", { exact: true })).toBeVisible();
});
