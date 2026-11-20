import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const baseURL = process.env.LANDING_QA_URL ?? "http://127.0.0.1:3187";
const output = new URL("../artifacts/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const results = [];

async function revealPage(page) {
  const height = await page.evaluate(
    () => document.documentElement.scrollHeight,
  );
  for (let y = 0; y < height; y += 650) {
    await page.evaluate(
      (top) => window.scrollTo({ top, behavior: "instant" }),
      y,
    );
    await page.waitForTimeout(85);
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(1000);
}

async function noOverflow(page) {
  const dimensions = await page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assert.ok(
    dimensions.document <= dimensions.viewport &&
      dimensions.body <= dimensions.viewport,
    `Horizontal overflow: ${JSON.stringify(dimensions)}`,
  );
  return dimensions;
}

try {
  for (const reduced of [false, true]) {
    for (const width of [1440, 768, 375, 320]) {
      const context = await browser.newContext({
        viewport: { width, height: width === 375 ? 812 : 1000 },
        deviceScaleFactor: 1,
        reducedMotion: reduced ? "reduce" : "no-preference",
        locale: "vi-VN",
        timezoneId: "Asia/Saigon",
      });
      const page = await context.newPage();
      const consoleErrors = [];
      const pageErrors = [];
      const failedRequests = [];
      page.on("console", (message) => {
        if (message.type() === "error") consoleErrors.push(message.text());
      });
      page.on("pageerror", (error) => pageErrors.push(error.message));
      page.on("requestfailed", (request) =>
        failedRequests.push(
          `${request.method()} ${request.url()} ${request.failure()?.errorText}`,
        ),
      );
      const response = await page.goto(baseURL, { waitUntil: "networkidle" });
      assert.equal(response.status(), 200);
      await page.evaluate(() => document.fonts.ready);
      await page.waitForFunction(() => Boolean(window.SakugaFX));
      assert.match(await page.title(), /v2\.0\.0/);
      const tabs = page.getByRole("tab");
      assert.equal(await tabs.count(), 3);
      const snapshots = [];
      const visiblePanel = () => page.locator('[role="tabpanel"]:visible');
      for (let index = 0; index < 3; index++) {
        await tabs.nth(index).click();
        await page.waitForTimeout(reduced ? 20 : 450);
        assert.equal(
          await tabs.nth(index).getAttribute("aria-selected"),
          "true",
        );
        assert.equal(await visiblePanel().count(), 1);
        snapshots.push(await noOverflow(page));
      }
      // Independent profile selection + explicit, local-only schedule feedback.
      const publishPanel = visiblePanel();
      const profiles = publishPanel.locator(".studio-profile");
      const firstSelected = await profiles.first().getAttribute("aria-pressed");
      await profiles.first().click();
      assert.notEqual(
        await profiles.first().getAttribute("aria-pressed"),
        firstSelected,
      );
      await profiles.first().click();
      await publishPanel
        .locator('input[type="datetime-local"]')
        .fill("2026-12-01T19:30");
      await publishPanel.getByRole("button", { name: /Thử lên lịch/ }).click();
      assert.match(
        await publishPanel.locator(".studio-schedule-feedback").innerText(),
        /DEMO.*2 hồ sơ/,
      );
      // True switch feedback, waveform/speed/pitch changes, and state persistence.
      await tabs.nth(1).click();
      const editPanel = visiblePanel();
      const blur = editPanel.getByRole("switch", { name: "Che chữ Trung" });
      await blur.click();
      assert.equal(await blur.getAttribute("aria-checked"), "false");
      assert.equal(await editPanel.locator(".studio-edit-blurred").count(), 0);
      await blur.click();
      const burn = editPanel.getByRole("switch", { name: "Burn sub Việt" });
      await burn.click();
      assert.equal(await editPanel.locator(".studio-burned-sub").count(), 0);
      await burn.click();
      const waveformBefore = await editPanel
        .locator(".studio-waveform")
        .innerHTML();
      await editPanel
        .getByRole("button", { name: "1.15×", exact: true })
        .click();
      await editPanel
        .getByRole("button", { name: /Sáng, \+2 semitone/ })
        .click();
      assert.notEqual(
        await editPanel.locator(".studio-waveform").innerHTML(),
        waveformBefore,
      );
      assert.match(
        await editPanel.locator(".studio-waveform-head").innerText(),
        /1\.15×.*\+2/,
      );
      // Dragging and keyboard alternatives update the actual crop and translated sample.
      await tabs.nth(0).click();
      const vietsubPanel = visiblePanel();
      const crop = vietsubPanel.locator(".studio-crop");
      const cropBefore = await crop.getAttribute("style");
      await crop.focus();
      await crop.press("ArrowUp");
      assert.notEqual(await crop.getAttribute("style"), cropBefore);
      const box = await crop.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2, box.y - 40, { steps: 8 });
      await page.mouse.up();
      assert.notEqual(await crop.getAttribute("style"), cropBefore);
      await vietsubPanel
        .getByRole("button", { name: "Khớp chữ", exact: true })
        .click();
      assert.match(
        await vietsubPanel.locator(".studio-crop-status").innerText(),
        /Đã bắt trọn/,
      );
      const cropGeometry = await crop.boundingBox();
      const sceneGeometry = await vietsubPanel
        .locator(".studio-ocr-scene")
        .boundingBox();
      assert.ok(
        cropGeometry.height >= 44 &&
          cropGeometry.y + cropGeometry.height <=
            sceneGeometry.y + sceneGeometry.height + 1,
      );
      if (width === 320) {
        for (let step = 0; step < 12; step++)
          await crop.press("Shift+ArrowDown");
        const bottomCrop = await crop.boundingBox();
        const bottomScene = await vietsubPanel
          .locator(".studio-ocr-scene")
          .boundingBox();
        assert.ok(
          bottomCrop.y + bottomCrop.height <=
            bottomScene.y + bottomScene.height + 1,
          "Crop must stay inside narrow scene",
        );
        await crop.press("Home");
      }
      const sampleBefore = await vietsubPanel
        .locator(".studio-srt-body p")
        .innerText();
      await vietsubPanel
        .getByRole("button", { name: "Bilibili", exact: false })
        .click();
      assert.notEqual(
        await vietsubPanel.locator(".studio-srt-body p").innerText(),
        sampleBefore,
      );
      await vietsubPanel
        .getByRole("button", { name: "Douyin", exact: false })
        .click();
      const play = vietsubPanel.getByRole("button", {
        name: "Phát preview mô phỏng",
        exact: true,
      });
      await play.click();
      const timeBefore = await vietsubPanel
        .locator('input[type="range"]')
        .inputValue();
      await page.waitForTimeout(350);
      assert.notEqual(
        await vietsubPanel.locator('input[type="range"]').inputValue(),
        timeBefore,
      );
      await vietsubPanel
        .getByRole("button", { name: "Tạm dừng preview mô phỏng" })
        .click();
      // Roving arrow/Home/End tab navigation.
      await tabs.nth(0).focus();
      await tabs.nth(0).press("ArrowRight");
      assert.equal(await tabs.nth(1).getAttribute("aria-selected"), "true");
      assert.equal(
        await visiblePanel()
          .getByRole("button", { name: "1.15×", exact: true })
          .getAttribute("aria-pressed"),
        "true",
      );
      await tabs.nth(1).press("End");
      assert.equal(await tabs.nth(2).getAttribute("aria-selected"), "true");
      await tabs.nth(2).press("Home");
      assert.equal(await tabs.nth(0).getAttribute("aria-selected"), "true");
      // The combined 1-click control changes both the rate and pitch together.
      await tabs.nth(1).click();
      const quickVoice = visiblePanel().getByRole("switch", {
        name: "Tốc độ & cao độ 1-click",
        exact: true,
      });
      assert.equal(await quickVoice.getAttribute("aria-checked"), "true");
      await quickVoice.click();
      assert.match(
        await visiblePanel().locator(".studio-waveform-head").innerText(),
        /1\.00×.*0 st/,
      );
      await quickVoice.click();
      await tabs.nth(0).click();
      // All download surfaces share the exact v2.0.2 release target.
      const releaseLinks = await page
        .locator('a[href*="/releases/tag/"]')
        .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
      assert.ok(releaseLinks.length >= 3);
      assert.ok(
        releaseLinks.every(
          (link) =>
            link ===
            "https://github.com/qvinh8726/VietSub-Studio/releases/tag/v2.0.2",
        ),
      );
      if (reduced) {
        const animationNames = await page
          .locator(".aurora,.mq-track,.liquid-beam")
          .evaluateAll((elements) =>
            elements.map((element) => getComputedStyle(element).animationName),
          );
        assert.ok(animationNames.every((name) => name === "none"));
        assert.equal(await page.evaluate(() => window.SakugaFX.calm), true);
      }
      // Mobile navigation is keyboard dismissible.
      if (width < 1000) {
        await page
          .getByRole("button", { name: "Mở menu", exact: true })
          .click();
        assert.equal(await page.locator("#mobile-nav").isVisible(), true);
        await page.keyboard.press("Escape");
        assert.equal(await page.locator("#mobile-nav").isVisible(), false);
      }
      const pauseMarquee = page.locator(".mq-pause");
      await pauseMarquee.click();
      assert.equal(await pauseMarquee.getAttribute("aria-pressed"), "true");
      await pauseMarquee.press("Space");
      assert.equal(await pauseMarquee.getAttribute("aria-pressed"), "false");
      if (!reduced) {
        const motionState = await page
          .locator(".mq-track")
          .first()
          .evaluate((element) => getComputedStyle(element).animationPlayState);
        assert.equal(
          motionState,
          "running",
          "Keyboard resume must work while pause button retains focus",
        );
      }
      await revealPage(page);
      await noOverflow(page);
      const filename = `landing-${width}${reduced ? "-reduced-motion" : ""}.png`;
      await page.screenshot({
        path: fileURLToPath(new URL(filename, output)),
        fullPage: true,
        animations: "disabled",
      });
      if (width === 1440 && !reduced) {
        await page.locator(".studio-showcase").screenshot({
          path: fileURLToPath(new URL("studio-vietsub.png", output)),
          animations: "disabled",
        });
        await tabs.nth(1).click();
        await page.waitForTimeout(450);
        await page.locator(".studio-showcase").screenshot({
          path: fileURLToPath(new URL("studio-auto-edit.png", output)),
          animations: "disabled",
        });
        await tabs.nth(2).click();
        await page.waitForTimeout(450);
        await page.locator(".studio-showcase").screenshot({
          path: fileURLToPath(new URL("studio-publish.png", output)),
          animations: "disabled",
        });
      }
      assert.deepEqual(
        consoleErrors,
        [],
        `Console errors at ${width}/${reduced}`,
      );
      assert.deepEqual(
        pageErrors,
        [],
        `Page/hydration errors at ${width}/${reduced}`,
      );
      assert.deepEqual(
        failedRequests,
        [],
        `Failed requests at ${width}/${reduced}`,
      );
      results.push({
        width,
        reducedMotion: reduced,
        consoleErrors,
        pageErrors,
        failedRequests,
        horizontalOverflow: false,
        demoTabs: 3,
        interactions: "pass",
        screenshot: filename,
        dimensions: snapshots,
      });
      console.log(
        `PASS ${width}px ${reduced ? "reduced-motion" : "normal"}: 3 tabs, crop drag/keys, switches, waveform, profiles, schedule, keyboard, console and overflow.`,
      );
      await context.close();
    }
  }
  await writeFile(
    new URL("browser-qa.json", output),
    JSON.stringify(
      {
        baseURL,
        browser: browser.version(),
        checkedAt: new Date().toISOString(),
        results,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
