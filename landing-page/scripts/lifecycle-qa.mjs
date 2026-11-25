import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "@playwright/test";

const output = new URL("../artifacts/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "no-preference",
  });
  const page = await context.newPage();
  await page.goto(process.env.LANDING_QA_URL ?? "http://127.0.0.1:3187", {
    waitUntil: "networkidle",
  });
  await page.waitForFunction(() => Boolean(window.SakugaFX));
  await page.locator(".studio-tabs").scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  // Smoke is drawn at document coordinates, even after scroll.
  const puff = await page.evaluate(() => {
    let captured;
    const fx = window.SakugaFX;
    const original = fx.puff;
    fx.puff = (x, y, options) => {
      captured = { x, y, options };
      original(x, y, options);
    };
    const tab = document.querySelectorAll('[role="tab"]')[1];
    const rect = tab.getBoundingClientRect();
    tab.click();
    fx.puff = original;
    return { captured, expectedY: rect.bottom + scrollY - 5, scrollY };
  });
  assert.ok(puff.captured);
  assert.equal(puff.captured.y, puff.expectedY);
  await page.waitForTimeout(1100);
  const clockIdle = await page.evaluate(() => window.SakugaFX.now);
  await page.waitForTimeout(350);
  assert.equal(
    await page.evaluate(() => window.SakugaFX.now),
    clockIdle,
    "Sakuga clock must stop when finite VFX finishes",
  );
  await page.evaluate(() =>
    window.SakugaFX.puff(300, scrollY + 200, { size: 14, life: 2 }),
  );
  await page.waitForTimeout(100);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(() => window.SakugaFX.calm);
  const stoppedForReduction = await page.evaluate(() => window.SakugaFX.now);
  await page.waitForTimeout(300);
  assert.equal(
    await page.evaluate(() => window.SakugaFX.now),
    stoppedForReduction,
    "Live preference change must cancel effects",
  );
  const canvasCleared = await page.evaluate(() => {
    const canvas = document.querySelector("canvas.sakuga-fx");
    return (
      !canvas ||
      !canvas
        .getContext("2d")
        .getImageData(0, 0, canvas.width, canvas.height)
        .data.some((value) => value !== 0)
    );
  });
  assert.equal(
    canvasCleared,
    true,
    "Reduced motion must clear every pixel even while scrolled",
  );
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.waitForFunction(() => !window.SakugaFX.calm);
  await page.evaluate(() => {
    window.SakugaFX.puff(200, 200, { life: 2 });
    window.SakugaFX.release();
  });
  const stoppedForRelease = await page.evaluate(() => window.SakugaFX.now);
  await page.waitForTimeout(300);
  assert.equal(
    await page.evaluate(() => window.SakugaFX.now),
    stoppedForRelease,
  );
  assert.equal(await page.locator("canvas.sakuga-fx").count(), 0);
  await page.evaluate(() => {
    window.SakugaFX.retain();
    window.SakugaFX.puff(200, 200, { life: 0.3 });
  });
  assert.equal(await page.locator("canvas.sakuga-fx").count(), 1);
  // The download control keeps its real target, with warm slash/burst feedback.
  const cta = await page.evaluate(() => {
    const fx = window.SakugaFX;
    const original = { slash: fx.slash, burst: fx.burst };
    const calls = [];
    fx.slash = (...args) => {
      calls.push("slash");
      original.slash(...args);
    };
    fx.burst = (...args) => {
      calls.push("burst");
      original.burst(...args);
    };
    const link = document.querySelector(".hero-actions .cta-sun");
    link.addEventListener("click", (event) => event.preventDefault(), {
      once: true,
    });
    link.click();
    fx.slash = original.slash;
    fx.burst = original.burst;
    return { calls, href: link.href };
  });
  assert.deepEqual(cta.calls, ["burst", "slash"]);
  assert.equal(
    cta.href,
    "https://github.com/qvinh8726/VietSub-Studio/releases/tag/v2.1.0",
  );
  await page.waitForTimeout(900);
  const result = {
    finiteClockStops: true,
    liveReducedMotionCancels: true,
    releaseCancelsAndRemovesCanvas: true,
    retainRestoresEffects: true,
    scrolledSmokeDocumentCoordinates: true,
    downloadBurstSlash: true,
  };
  await writeFile(
    new URL("lifecycle-qa.json", output),
    JSON.stringify(result, null, 2),
  );
  console.log(
    "PASS Sakuga: idle clock, live reduced-motion, release/retain, scroll-correct smoke, CTA burst/slash.",
  );
  await context.close();
} finally {
  await browser.close();
}
