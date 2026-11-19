import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

const output = new URL("../artifacts/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const results = [];
try {
  for (const width of [1440, 375]) {
    const context = await browser.newContext({
      viewport: { width, height: 1000 },
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    await page.goto(process.env.LANDING_QA_URL ?? "http://127.0.0.1:3187", {
      waitUntil: "networkidle",
    });
    await page.evaluate(() => document.fonts.ready);
    for (let index = 0; index < 3; index++) {
      await page.getByRole("tab").nth(index).click();
      const result = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      const violations = result.violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        description: v.description,
        nodes: v.nodes.map((n) => ({
          target: n.target,
          summary: n.failureSummary,
        })),
      }));
      results.push({ width, tab: index, violations });
      console.log(JSON.stringify({ width, tab: index, violations }, null, 2));
    }
    await context.close();
  }
  await writeFile(
    new URL("accessibility-qa.json", output),
    JSON.stringify(results, null, 2),
  );
  if (results.some((result) => result.violations.length)) process.exitCode = 1;
} finally {
  await browser.close();
}
