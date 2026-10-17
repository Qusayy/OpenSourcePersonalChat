import { expect, type Page, test } from "@playwright/test";
import { reseed } from "../support";
import { answerOf, ask, CONVERSATION, DONE, end, fakeChat, push, START } from "./helpers";

/* A code project: the files an answer writes appear in the project panel as
   they are written, beside the thread on a wide screen and in a drawer on a
   narrow one, and the whole of it downloads as a zip. */

async function writeProject(page: Page) {
  await ask(page, "build me a tiny app");
  await push(page, "conversation", { ...CONVERSATION, id: "c0ffee" });
  await push(page, "start", START);
  await push(page, "token", { t: "Here it is.\n\n", ttft_ms: 50 });
  await push(page, "token", { t: "### app.py\n```python\n" });
  await push(page, "file", { type: "open", path: "app.py", lang: "python" });
  await push(page, "token", { t: "print('hi')\n" });
  await push(page, "file", { type: "line", path: "app.py", text: "print('hi')\n" });
}

test.beforeEach(async ({ page }) => {
  reseed();
  await fakeChat(page);
});

test.describe("on a wide screen", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("the panel opens beside the thread as the first file is written", async ({ page }) => {
    await page.goto("/");
    await writeProject(page);
    const panel = page.locator("#project");
    await expect(panel).toBeVisible();
    await expect(page.locator("#main")).toHaveClass(/project-split/);
    await expect(panel.locator("#project-files [data-path]")).toHaveText(/app\.py/);
    await expect(panel.locator("#project-files")).toContainText("writing");
    await expect(panel.locator("#project-viewer code")).toContainText("print('hi')");
    // The answer shows the file as a chip, not as code.
    await expect(answerOf(page).locator(".file-chip")).toContainText("app.py");
    await expect(answerOf(page).locator(".prose pre")).toHaveCount(0);

    await push(page, "token", { t: "print('there')\n```\n" });
    await push(page, "file", { type: "line", path: "app.py", text: "print('there')\n" });
    await push(page, "file", {
      type: "close",
      path: "app.py",
      lang: "python",
      lines: 2,
      version: 1,
      kept: true,
    });
    await expect(panel.locator("#project-viewer code")).toContainText("print('there')");
    await expect(panel.locator("#project-viewer")).toContainText("version 1");
    await expect(page.locator("#project-zip")).toHaveAttribute(
      "href",
      "/api/conversations/c0ffee/project.zip",
    );
    await page.screenshot({ path: "test-results/project-wide.png" });
  });

  test("Close takes the panel away and the top bar brings it back", async ({ page }) => {
    await page.goto("/");
    await writeProject(page);
    await page.locator("#project-close").click();
    await expect(page.locator("#project")).toHaveCount(0);
    await expect(page.locator("#main")).not.toHaveClass(/project-split/);
    await page.locator("#project-btn").click();
    await expect(page.locator("#project")).toBeVisible();
  });

  test("a file's chip in an answer opens it in the panel", async ({ page }) => {
    await page.goto("/");
    await writeProject(page);
    await page.locator("#project-close").click();
    await answerOf(page).locator(".file-chip").click();
    await expect(page.locator("#project")).toBeVisible();
    await expect(page.locator('#project-files [aria-current="true"]')).toContainText("app.py");
  });
});

test.describe("on a narrow screen", () => {
  test.use({ viewport: { width: 900, height: 800 } });

  test("the project waits in the top bar and opens as a drawer", async ({ page }) => {
    await page.goto("/");
    await writeProject(page);
    await expect(page.locator("#project")).toHaveCount(0);
    const button = page.locator("#project-btn");
    await expect(button).toBeVisible();
    await button.click();
    const drawer = page.locator("#project-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer.locator("#project-viewer code")).toContainText("print('hi')");
    await page.screenshot({ path: "test-results/project-narrow.png" });
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
  });
});

test("a new chat starts with no project", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await writeProject(page);
  await push(page, "done", DONE);
  await end(page);
  await page.locator("#new-chat").click();
  await expect(page.locator("#project-btn")).toHaveCount(0);
  await expect(page.locator("#project")).toHaveCount(0);
});
