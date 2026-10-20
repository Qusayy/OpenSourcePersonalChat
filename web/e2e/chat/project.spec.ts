import { expect, type Page, test } from "@playwright/test";
import { reseed } from "../support";
import {
  answerOf,
  ask,
  CONVERSATION,
  called,
  DONE,
  end,
  fakeChat,
  openStored,
  order,
  push,
  reply,
  START,
} from "./helpers";

/* A code project: the files the model writes with its calls appear in the
   project panel as they are written, beside the thread on a wide screen and
   in a drawer on a narrow one, and the whole of it downloads as a zip. In the
   answer, a file being written is a chip where the model wrote it, and the
   call's card takes its place once the call has been made. */

/** A reply that says a line and goes on to write app.py, a line of it so far. */
async function writeProject(page: Page) {
  await ask(page, "build me a tiny app");
  await push(page, "conversation", { ...CONVERSATION, id: "c0ffee" });
  await push(page, "step", { id: "reply-1", kind: "model", status: "running", label: "Writing" });
  await push(page, "start", START);
  await push(page, "token", { t: "Here it is.", ttft_ms: 50 });
  // The call's text is not shown; the file it writes is, line by line.
  await push(page, "file", { type: "open", path: "app.py", lang: "python" });
  await push(page, "file", { type: "line", path: "app.py", text: "print('hi')\n" });
}

/** The rest of it: the last line, the call made, the file kept. */
async function finishWrite(page: Page) {
  await push(page, "file", { type: "line", path: "app.py", text: "print('there')\n" });
  await push(page, "step", {
    id: "reply-1",
    kind: "model",
    status: "ok",
    label: "Called Write file",
  });
  await called(page, 0, {
    label: "Write file",
    tool: "write_file",
    card: "file",
    ms: 4,
    data: {
      path: "app.py",
      lang: "python",
      content: "print('hi')\nprint('there')\n",
      version: 1,
      lines: 2,
      created: true,
      action: "write",
    },
  });
  await push(page, "file", {
    type: "close",
    path: "app.py",
    lang: "python",
    lines: 2,
    version: 1,
    kept: true,
  });
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
    // The answer shows the file as a chip where it is being written, not as code.
    const chip = answerOf(page).locator('[data-part="file"]');
    await expect(chip).toContainText("app.py");
    await expect(chip).toHaveAttribute("data-state", "writing");
    await expect(chip).toContainText("writing · 1 line");
    await expect(answerOf(page).locator("pre")).toHaveCount(0);
    expect(await order(page)).toEqual(["text", "file"]);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "writing");

    await finishWrite(page);
    await expect(panel.locator("#project-viewer code")).toContainText("print('there')");
    await expect(panel.locator("#project-viewer")).toContainText("version 1");
    await expect(page.locator("#project-zip")).toHaveAttribute(
      "href",
      "/api/conversations/c0ffee/project.zip",
    );
    // The call has been made: its card stands where the chip was.
    await expect(answerOf(page).locator('[data-part="file"]')).toHaveCount(0);
    const card = answerOf(page).locator('figure[data-card="file"]');
    await expect(card.locator("figcaption")).toContainText("Created file");
    await expect(card.locator(".file-chip")).toContainText("version 1 · 2 lines");
    expect(await order(page)).toEqual(["text", "cards"]);

    await reply(page, 2, { say: "Run it with python app.py." });
    await push(page, "done", { ...DONE, tool_calls: 1 });
    await end(page);
    expect(await order(page)).toEqual(["text", "cards", "text"]);
    await page.screenshot({ path: "test-results/project-wide.png" });
  });

  test("an edit says what it changed, and the panel shows the file as it now is", async ({
    page,
  }) => {
    await page.goto("/");
    await ask(page, "fix the crash");
    await push(page, "conversation", { ...CONVERSATION, id: "c0ffee" });
    await reply(page, 1, { say: "`app` is used before it exists.", called: "Edit file" });
    const fixed = 'from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/")\n';
    await called(page, 0, {
      label: "Edit file",
      tool: "edit_file",
      card: "file",
      ms: 3,
      data: {
        path: "app/main.py",
        lang: "python",
        version: 3,
        lines: 5,
        content: fixed,
        action: "edit",
        line: 3,
        removed: ['@app.get("/")'],
        added: ["app = FastAPI()", "", '@app.get("/")'],
      },
    });
    // The file comes after the call, as it was kept: no chip stands in for it.
    await push(page, "file", { type: "open", path: "app/main.py", lang: "python" });
    for (const line of fixed.split(/(?<=\n)/)) {
      await push(page, "file", { type: "line", path: "app/main.py", text: line });
    }
    await push(page, "file", {
      type: "close",
      path: "app/main.py",
      lang: "python",
      lines: 5,
      version: 3,
      kept: true,
    });
    await expect(answerOf(page).locator('[data-part="file"]')).toHaveCount(0);
    const card = answerOf(page).locator('figure[data-card="file"]');
    await expect(card.locator("figcaption")).toContainText("Edited fileapp/main.py");
    await expect(card.locator(".file-chip")).toContainText("version 3 · 5 lines");
    const diff = card.locator('[data-part="diff"]');
    await expect(diff.locator("header")).toHaveText("from line 3");
    await expect(diff.locator('[data-sign="-"]')).toHaveText(['- @app.get("/")']);
    await expect(diff.locator('[data-sign="+"]')).toHaveText([
      "+ app = FastAPI()",
      "+ ",
      '+ @app.get("/")',
    ]);
    await expect(page.locator("#project-viewer code")).toContainText("app = FastAPI()");
    await expect(page.locator("#project-viewer")).toContainText("version 3");
    await card.screenshot({ path: "test-results/edit-card.png" });
  });

  test("Close takes the panel away and the top bar brings it back", async ({ page }) => {
    await page.goto("/");
    await writeProject(page);
    // Beside the thread, the button controls the panel there.
    await expect(page.locator("#project-btn")).toHaveAttribute("aria-controls", "project");
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
    await expect(page.locator('#project-files [aria-selected="true"]')).toContainText("app.py");
  });

  test("a file whose call was never made leaves the answer when it is stopped", async ({
    page,
  }) => {
    await page.goto("/");
    await writeProject(page);
    await expect(answerOf(page).locator('[data-part="file"]')).toHaveCount(1);
    await push(page, "step", { id: "reply-1", kind: "model", status: "stopped", label: "Writing" });
    await push(page, "done", { ...DONE, cancelled: true });
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await expect(answerOf(page).locator('[data-part="file"]')).toHaveCount(0);
    await expect(answerOf(page).locator(".prose")).toHaveText("Here it is.");
  });
});

test.describe("the tree of a project's files", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  /** The rows on screen as "  name" by level, a folder with "/", or "/+" closed. */
  const rows = (page: Page) =>
    page.locator('#project-files [role="treeitem"]').evaluateAll((items) =>
      items.map((el) => {
        const level = Number(el.getAttribute("aria-level")) - 1;
        const name = el.querySelector("span.flex-1")?.textContent ?? "";
        const folder = el.hasAttribute("data-folder");
        const closed = el.getAttribute("aria-expanded") === "false";
        return `${"  ".repeat(level)}${name}${folder ? (closed ? "/+" : "/") : ""}`;
      }),
    );
  const folder = (page: Page, path: string) =>
    page.locator(`#project-files [data-folder="${path}"]`);
  const file = (page: Page, path: string) => page.locator(`#project-files [data-path="${path}"]`);

  /** The seeded project, in its panel beside the thread. */
  async function openSeeded(page: Page) {
    await page.goto("/");
    await openStored(page, 3);
    await page.locator("#project-btn").click();
    await expect(page.locator("#project")).toBeVisible();
  }

  test("lists folders first, a level in for each, with the file shown filled", async ({ page }) => {
    await openSeeded(page);
    await expect(page.locator("#project")).toContainText("6 files");
    const tree = page.locator("#project-files");
    await expect(tree).toHaveAttribute("role", "tree");
    await expect(tree).toHaveAccessibleName("Project files");
    expect(await rows(page)).toEqual([
      "app/",
      "  routes/",
      "    health.py",
      "    users.py",
      "  main.py",
      "tests/",
      "  test_main.py",
      "README.md",
      "requirements.txt",
    ]);
    // Nothing chosen yet: the first file in the tree is the one shown.
    await expect(file(page, "app/routes/health.py")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#project-viewer")).toContainText("app/routes/health.py · version 1");
    await expect(file(page, "app/main.py")).toHaveAttribute("aria-selected", "false");
    // Each line count, fixed-width; a hairline down from each folder a row is in.
    await expect(file(page, "app/routes/users.py")).toContainText("9");
    await expect(file(page, "app/routes/users.py").locator(".tree-guide")).toHaveCount(2);
    await expect(folder(page, "app")).toHaveAttribute("aria-expanded", "true");
    await expect(folder(page, "app")).not.toContainText("files");
    await file(page, "app/main.py").click();
    await expect(file(page, "app/main.py")).toHaveAttribute("aria-selected", "true");
    const name = file(page, "app/main.py").locator("span.flex-1");
    expect(await name.evaluate((el) => getComputedStyle(el).fontWeight)).toBe("600");
    await expect(page.locator("#project-viewer code")).toContainText("app = FastAPI()");
    await page.locator("#project").screenshot({ path: "test-results/project-tree.png" });
  });

  test("folders open and close one at a time and all at once, and stay as left", async ({
    page,
  }) => {
    await openSeeded(page);
    await folder(page, "tests").click();
    await expect(folder(page, "tests")).toHaveAttribute("aria-expanded", "false");
    await expect(folder(page, "tests")).toContainText("1 file");
    await expect(file(page, "tests/test_main.py")).toHaveCount(0);
    await folder(page, "tests").click();
    await expect(file(page, "tests/test_main.py")).toBeVisible();

    const expand = page.getByRole("button", { name: "Expand all" });
    const collapse = page.getByRole("button", { name: "Collapse all" });
    await expect(expand).toBeDisabled();
    await collapse.click();
    expect(await rows(page)).toEqual(["app/+", "tests/+", "README.md", "requirements.txt"]);
    await expect(folder(page, "app")).toContainText("3 files");
    await expect(collapse).toBeDisabled();
    await expand.click();
    expect(await rows(page)).toHaveLength(9);

    // Left with routes closed, it is closed when the conversation is opened again.
    await folder(page, "app/routes").click();
    await page.reload();
    await openStored(page, 3);
    await page.locator("#project-btn").click();
    await expect(folder(page, "app/routes")).toHaveAttribute("aria-expanded", "false");
    await expect(folder(page, "app")).toHaveAttribute("aria-expanded", "true");
  });

  test("is one stop on Tab, walked with the arrow keys", async ({ page }) => {
    await openSeeded(page);
    const focused = () =>
      page.evaluate(() => {
        const el = document.activeElement;
        return el?.getAttribute("data-path") ?? `${el?.getAttribute("data-folder")}/`;
      });
    // One row takes Tab: the file shown.
    await expect(page.locator('#project-files [tabindex="0"]')).toHaveCount(1);
    await file(page, "app/routes/health.py").focus();
    await page.keyboard.press("ArrowUp");
    expect(await focused()).toBe("app/routes/");
    await page.keyboard.press("ArrowLeft"); // closes routes
    await expect(folder(page, "app/routes")).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("ArrowLeft"); // up to app
    expect(await focused()).toBe("app/");
    await page.keyboard.press("ArrowRight"); // app is open: into it
    expect(await focused()).toBe("app/routes/");
    await page.keyboard.press("ArrowRight"); // opens routes
    await expect(folder(page, "app/routes")).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("ArrowRight"); // into routes
    expect(await focused()).toBe("app/routes/health.py");
    await page.keyboard.press("ArrowDown");
    expect(await focused()).toBe("app/routes/users.py");
    await page.keyboard.press("Enter");
    await expect(file(page, "app/routes/users.py")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#project-viewer")).toContainText("app/routes/users.py");
    await page.keyboard.press("End");
    expect(await focused()).toBe("requirements.txt");
    await page.keyboard.press("Space");
    await expect(file(page, "requirements.txt")).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Home");
    expect(await focused()).toBe("app/");
    await page.keyboard.press("Space");
    await expect(folder(page, "app")).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator('#project-files [tabindex="0"]')).toHaveCount(1);
  });

  test("a chip for a file in a closed folder opens its folders and shows it", async ({ page }) => {
    await openSeeded(page);
    await page.getByRole("button", { name: "Collapse all" }).click();
    await page.locator("#project-close").click();
    await answerOf(page).locator('figure[data-card="file"] .file-chip').click();
    await expect(page.locator("#project")).toBeVisible();
    await expect(folder(page, "app")).toHaveAttribute("aria-expanded", "true");
    await expect(folder(page, "tests")).toHaveAttribute("aria-expanded", "false");
    await expect(file(page, "app/main.py")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#project-viewer")).toContainText("app/main.py · version 2");
  });

  test("a file moved or removed down the stream is moved or removed in the tree", async ({
    page,
  }) => {
    await page.goto("/");
    await ask(page, "tidy the project");
    await push(page, "conversation", { ...CONVERSATION, id: "c0ffee" });
    const kept = async (path: string, text: string) => {
      await push(page, "file", { type: "open", path, lang: "python" });
      await push(page, "file", { type: "line", path, text });
      await push(page, "file", {
        type: "close",
        path,
        lang: "python",
        lines: 1,
        version: 1,
        kept: true,
      });
    };
    await kept("lib/util.py", "def util(): ...\n");
    await kept("z.txt", "z\n");
    await kept("app.py", "print('app')\n");
    await expect(file(page, "app.py")).toHaveAttribute("aria-selected", "true");
    expect(await rows(page)).toEqual(["lib/", "  util.py", "app.py", "z.txt"]);

    // The file shown goes where it was moved, and shows what it held there.
    await push(page, "file", { type: "move", from: "app.py", to: "src/app.py", version: 2 });
    expect(await rows(page)).toEqual(["lib/", "  util.py", "src/", "  app.py", "z.txt"]);
    await expect(file(page, "src/app.py")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#project-viewer")).toContainText("src/app.py · version 2");
    await expect(page.locator("#project-viewer code")).toHaveText("print('app')\n");

    // Removed, the next file in the tree is shown in its place.
    await push(page, "file", { type: "remove", path: "src/app.py" });
    await expect(file(page, "src/app.py")).toHaveCount(0);
    expect(await rows(page)).toEqual(["lib/", "  util.py", "z.txt"]);
    await expect(file(page, "z.txt")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#project")).toContainText("2 files");
    await push(page, "done", DONE);
    await end(page);
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
    // On a narrow screen it controls the drawer.
    await expect(button).toHaveAttribute("aria-controls", "project-drawer");
    await button.click();
    const drawer = page.locator("#project-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer.locator("#project-viewer code")).toContainText("print('hi')");
    await expect(drawer.locator('#project-files [data-path="app.py"]')).toHaveAttribute(
      "aria-selected",
      "true",
    );
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
