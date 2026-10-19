import { expect, test } from "@playwright/test";
import { forward, reseed } from "../support";
import {
  answer,
  answerOf,
  ask,
  CONVERSATION,
  called,
  DONE,
  end,
  fakeChat,
  openStored,
  order,
  posts,
  push,
  question,
  reply,
  START,
} from "./helpers";

/* The thread: how a question and an answer are set, a long message folded,
   what can be done with an answer, and a stored conversation drawn again. */

test.beforeEach(async ({ page }) => {
  reseed();
  await fakeChat(page);
  await page.goto("/");
});

test.describe("a stored conversation", () => {
  test("is drawn again from its markdown, with what each answer cost", async ({ page }) => {
    await openStored(page, 1);
    const prose = answerOf(page).locator(".prose");
    // An answer's headings start one level down: the page has its own h1.
    await expect(prose.locator("h2")).toHaveText("Decisions");
    await expect(prose.locator("h3")).toHaveText("What was agreed");
    await expect(prose.locator("ol > li")).toHaveCount(3);
    await expect(prose.locator("ul > li")).toHaveCount(2);
    await expect(prose.locator("blockquote")).toContainText("A larger model waits");
    await expect(prose.locator("table th")).toHaveText(["Item", "Outcome"]);
    await expect(prose.locator("a")).toHaveAttribute("href", "https://example.org/aurora");
    await expect(prose.locator("li code").first()).toHaveText("4096");
    await expect(prose.locator(".codeblock pre")).toContainText("AURORA_N_CTX = 4096");
    // A stored answer is set straight into its element: no streaming containers.
    await expect(prose.locator(":scope > div.codeblock")).toHaveCount(1);
    await expect(prose.locator(":scope > h2")).toHaveCount(1);

    const cost = answerOf(page).locator('[data-part="cost"]');
    await expect(cost).toContainText("8.4 tok/s");
    await expect(cost).toContainText("1.83s to first token");
    await expect(cost).toContainText("148 tokens");
    // A stored answer did not keep how long the whole of it took.
    await expect(cost).not.toContainText("to answer");
    await expect(page.locator("#ctx-value")).toHaveText("360");
  });

  test("an answer's correction is drawn again under it, above what it cost", async ({ page }) => {
    await openStored(page, 0);
    const answer = answerOf(page);
    const correction = answer.locator('[data-part="correction"]');
    await expect(correction).toHaveText(
      "Not in what was looked up: nine. Weather returned: Reykjavík, Iceland: Partly cloudy, 7.4°C.",
    );
    await expect(correction.locator(".icon")).toHaveCount(1);
    // Between the answer and its footer.
    const order = await answer.evaluate((el) =>
      [...el.querySelectorAll('[data-part="correction"], [data-part="cost"], .prose')].map(
        (node) => (node as HTMLElement).dataset.part ?? "prose",
      ),
    );
    expect(order).toEqual(["prose", "correction", "cost"]);
  });

  test("how a stored answer ran is read back from its steps", async ({ page }) => {
    await openStored(page, 0);
    const toggle = answerOf(page).locator('[data-act="ledger"]');
    await toggle.click();
    const rows = answerOf(page).locator('[data-part="ledger"] li');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText("choice");
    await expect(rows.nth(0)).toContainText(/weather/i);
    await expect(rows.nth(0)).toContainText("read 18 of 667 · wrote 12");
    await expect(rows.nth(2)).toContainText("choice again");
    await expect(rows.nth(3)).toContainText("answer");
    await expect(answerOf(page).locator('[data-part="ledger"]')).toContainText("run 4.02s");
  });

  test("an answer whose steps were not kept offers nothing to open", async ({ page }) => {
    await openStored(page, 1);
    await expect(answerOf(page).locator('[data-act="ledger"]')).toHaveCount(0);
  });

  test("an answer with nothing to correct has no correction", async ({ page }) => {
    await openStored(page, 1);
    await expect(answerOf(page).locator('[data-part="correction"]')).toHaveCount(0);
  });

  test("lists keep their markers and a fourth-level heading its weight", async ({ page }) => {
    await openStored(page, 1);
    const styles = await page.evaluate(() => {
      const of = (selector: string) => {
        const el = document.querySelector(`[data-part="answer"] .prose ${selector}`);
        return el ? getComputedStyle(el) : null;
      };
      return {
        ol: of("ol")?.listStyleType,
        ul: of("ul")?.listStyleType,
        h4: of("h4")?.fontWeight,
        h4Margin: of("h4")?.marginTop,
      };
    });
    expect(styles.ol).toBe("decimal");
    expect(styles.ul).toBe("disc");
    expect(styles.h4).toBe("700");
    expect(Number.parseFloat(styles.h4Margin ?? "0")).toBeGreaterThan(10);
  });

  test("a code block's Copy button copies the code", async ({ page }) => {
    await openStored(page, 1);
    const copy = answerOf(page).locator(".codeblock [data-copy]");
    await expect(copy).toHaveText("Copy");
    await copy.click();
    await expect(copy).toHaveText("Copied");
    await expect(copy).toHaveText("Copy", { timeout: 4000 });
  });

  test("right-to-left text lays out right-to-left on its own", async ({ page }) => {
    await openStored(page, 2);
    const directions = await page.evaluate(() => {
      const dir = (selector: string) => {
        const el = document.querySelector(selector);
        return el ? getComputedStyle(el).direction : null;
      };
      return {
        question: dir('[data-part="question"] [dir="auto"]'),
        answer: dir('[data-part="answer"] .prose'),
        code: dir('[data-part="answer"] .prose code'),
      };
    });
    expect(directions.question).toBe("rtl");
    expect(directions.answer).toBe("rtl");
    await expect(answerOf(page).locator(".prose code")).toHaveText("print(1 + 1)");
  });

  test("one kept before its replies were draws its tool results above its text", async ({
    page,
  }) => {
    await openStored(page, 0);
    const cards = answerOf(page).locator('[data-part="cards"] > *');
    // Eight results and one failure.
    await expect(cards).toHaveCount(9);
    const box = await page.evaluate(() => {
      const cardsEl = document.querySelector('[data-part="cards"]')?.getBoundingClientRect();
      const prose = document.querySelector('[data-part="answer"] .prose')?.getBoundingClientRect();
      return Boolean(cardsEl && prose && cardsEl.bottom <= prose.top);
    });
    expect(box).toBe(true);
    expect(await order(page)).toEqual(["cards", "text"]);
    await expect(answerOf(page).locator('[data-part="thought"]')).toHaveCount(0);
  });

  test("one kept as it was said is drawn in that order: thoughts, words and cards", async ({
    page,
  }) => {
    await openStored(page, 3);
    await expect(question(page)).toHaveText("Why does app/main.py crash on start? Fix it.");
    expect(await order(page)).toEqual([
      "thought",
      "text",
      "cards",
      "thought",
      "cards",
      "thought",
      "text",
    ]);
    // Each thought is folded, and how long it took was not kept.
    const thoughts = answerOf(page).locator('[data-part="thought"]');
    await expect(thoughts.locator("summary")).toHaveText(["Thought", "Thought", "Thought"]);
    await expect(thoughts.first()).not.toHaveAttribute("open", "");
    await thoughts.first().locator("summary").click();
    await expect(thoughts.first().locator("summary + div")).toHaveText(
      "The decorator uses app before app = FastAPI() exists.",
    );
    const prose = answerOf(page).locator(".prose");
    await expect(prose.first()).toContainText("is used before it is created.");
    await expect(prose.first().locator("code")).toHaveText("app");
    // The edit, with what it changed, where it was made; then the run.
    const edit = answerOf(page).locator('figure[data-card="file"]');
    await expect(edit.locator("figcaption")).toContainText("Edited fileapp/main.py");
    await expect(edit.locator('[data-sign="-"]')).toHaveText(['- @app.get("/")']);
    await expect(edit.locator('[data-sign="+"]')).toHaveCount(3);
    const run = answerOf(page).locator('figure[data-card="python"]');
    await expect(run.locator("figcaption")).toContainText("exited 0");
    await expect(prose.nth(1).locator(".codeblock pre")).toHaveText(
      "uvicorn app.main:app --reload",
    );
    // The file chip opens the file as the project kept it.
    await edit.locator(".file-chip").click();
    await expect(page.locator("#project-viewer code")).toContainText("app = FastAPI()");

    // How it ran, in the words of a run whose replies chose their calls.
    await answerOf(page).locator('[data-act="ledger"]').click();
    const rows = answerOf(page).locator('[data-part="ledger"] li');
    await expect(rows).toHaveCount(5);
    await expect(rows.nth(0)).toContainText("chose");
    await expect(rows.nth(0)).toContainText("Edit file");
    await expect(rows.nth(1)).toContainText("tool");
    await expect(rows.nth(2)).toContainText("Run file");
    await expect(rows.nth(4)).toContainText("answer");
    await expect(answerOf(page).locator('[data-part="cost"]')).toContainText("10.4 tok/s");
  });

  test("Copy on an answer said in parts copies all it said and none of what it thought", async ({
    page,
  }) => {
    await openStored(page, 3);
    // What the page hands the clipboard, kept where the test can read it.
    await page.evaluate(() => {
      const kept = window as unknown as { __copied?: string };
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (text: string) => {
            kept.__copied = text;
          },
        },
      });
    });
    // The answer's own Copy, not a code block's.
    await answerOf(page).locator('[data-act="copy"]').click();
    await expect(page.locator("#toasts")).toContainText("Answer copied");
    const copied = await page.evaluate(
      () => (window as unknown as { __copied?: string }).__copied ?? "",
    );
    expect(copied).toContain("is used before it is created.");
    expect(copied).toContain("uvicorn app.main:app --reload");
    expect(copied).not.toContain("The decorator uses app");
  });
});

test.describe("a file written out in an answer's text", () => {
  const said = "Here it is.\n\n### app.py\n```python\nprint('hi')\n```";

  /** Serve the right-to-left conversation with an answer that wrote a file
   *  out in its text, kept with these segments (null: kept before them). */
  async function storedWith(page: import("@playwright/test").Page, segments: unknown) {
    await page.unrouteAll();
    await page.route(/\/api\/conversations\/[0-9a-f]+$/, async (route) => {
      const response = await forward(route);
      const body = await response.json();
      for (const m of body.messages) {
        if (m.role !== "assistant") continue;
        m.content = said;
        m.segments = segments;
        m.tool_calls = [];
      }
      await route.fulfill({ response, json: body });
    });
    await openStored(page, 2);
  }

  test("is a chip in an answer kept from before files were written with calls", async ({
    page,
  }) => {
    await storedWith(page, null);
    const prose = answerOf(page).locator(".prose");
    await expect(prose.locator(".file-chip")).toContainText("app.py");
    await expect(prose.locator(".file-chip")).toContainText("1 line");
    await expect(prose.locator("pre")).toHaveCount(0);
  });

  test("is code in an answer kept as it was said", async ({ page }) => {
    await storedWith(page, [{ kind: "text", text: said }]);
    const prose = answerOf(page).locator(".prose");
    await expect(prose.locator(".codeblock pre")).toHaveText("print('hi')");
    await expect(prose.locator(".file-chip")).toHaveCount(0);
  });

  test("is code in a reply being written: its files are written with calls", async ({ page }) => {
    await ask(page, "show me");
    await push(page, "conversation", CONVERSATION);
    await push(page, "start", START);
    await push(page, "token", { t: "Here it is.\n\n### app.py\n```python\n", ttft_ms: 50 });
    await push(page, "token", { t: "print('hi')\n" });
    const prose = answerOf(page).locator(".prose");
    await expect(prose.locator("pre code")).toContainText("print('hi')");
    await expect(prose.locator(".file-chip")).toHaveCount(0);
    await push(page, "token", { t: "```" });
    await push(page, "done", DONE);
    await end(page);
    await expect(prose.locator(".codeblock pre")).toHaveText("print('hi')");
    await expect(prose.locator(".file-chip")).toHaveCount(0);
  });
});

test.describe("a long message", () => {
  test("is folded to five lines until asked for", async ({ page }) => {
    await openStored(page, 1);
    const q = question(page);
    await expect(q).toHaveAttribute("data-long", "true");
    const more = q.getByRole("button", { name: "Show all" });
    await expect(more).toHaveAttribute("aria-expanded", "false");
    const body = q.locator('[dir="auto"]');
    const folded = await body.evaluate((el) => el.getBoundingClientRect().height);
    const lineHeight = await body.evaluate((el) =>
      Number.parseFloat(getComputedStyle(el).lineHeight),
    );
    expect(Math.round(folded / lineHeight)).toBe(5);

    await more.click();
    const less = q.getByRole("button", { name: "Show less" });
    await expect(less).toHaveAttribute("aria-expanded", "true");
    expect(await body.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThan(folded);
    await less.click();
    await expect(q.getByRole("button", { name: "Show all" })).toBeVisible();
    // The same height to a tenth of a pixel: the box is measured in floating
    // point, and the two readings can differ in the fifth decimal place.
    expect(await body.evaluate((el) => el.getBoundingClientRect().height)).toBeCloseTo(folded, 1);
  });

  test("a short question is a heading and has nothing to unfold", async ({ page }) => {
    await openStored(page, 0);
    await expect(question(page)).not.toHaveAttribute("data-long", "true");
    await expect(question(page).getByRole("button")).toHaveCount(0);
  });

  test("line breaks in a question are kept", async ({ page }) => {
    await page.locator("#input").fill("one\ntwo");
    await page.locator("#send").click();
    await expect(question(page).locator("br")).toHaveCount(1);
    await expect(question(page)).toHaveText("onetwo");
  });

  test("markup in a question is shown, not run", async ({ page }) => {
    await ask(page, '<img src=x onerror="window.__pwned = 1"> & <b>bold</b>');
    await expect(question(page)).toHaveText(
      '<img src=x onerror="window.__pwned = 1"> & <b>bold</b>',
    );
    await expect(question(page).locator("img, b")).toHaveCount(0);
    await answer(page, "<script>window.__pwned = 2</script> **fine**");
    await expect(answerOf(page).locator(".prose strong")).toHaveText("fine");
    await expect(answerOf(page).locator(".prose script")).toHaveCount(0);
    expect(
      await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned),
    ).toBeUndefined();
  });
});

test.describe("what can be done with an answer", () => {
  test("Copy puts its markdown on the clipboard and says so", async ({ page }) => {
    await ask(page, "what is a token?");
    await answer(page, "A **piece** of a word.");
    await answerOf(page).getByRole("button", { name: "Copy" }).click();
    await expect(page.locator("#toasts")).toContainText("Answer copied");
  });

  test("Ask again sends the question it answered", async ({ page }) => {
    await ask(page, "what is a token?");
    await answer(page, "A piece of a word.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await answerOf(page).getByRole("button", { name: "Ask again" }).click();
    await expect
      .poll(async () => (await posts(page)).map((p) => p.message))
      .toEqual(["what is a token?", "what is a token?"]);
    await expect(question(page)).toHaveCount(2);
  });

  test("Ask again does nothing while another answer is being written", async ({ page }) => {
    await ask(page, "first");
    await answer(page, "One.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await ask(page, "second");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "stop");
    await answerOf(page).first().getByRole("button", { name: "Ask again" }).click();
    expect((await posts(page)).map((p) => p.message)).toEqual(["first", "second"]);
  });
});

test.describe("while an answer is being written", () => {
  test("finished blocks are left alone and only the last one is rewritten", async ({ page }) => {
    await ask(page, "tell me two things");
    await push(page, "conversation", CONVERSATION);
    await push(page, "start", START);
    await push(page, "token", { t: "First paragraph.\n\n", ttft_ms: 80 });
    await push(page, "token", { t: "Second " });
    const prose = answerOf(page).locator(".prose");
    await expect(prose.locator(":scope > div").first().locator("p")).toHaveText("First paragraph.");
    // Mark the settled paragraph; if it were rebuilt the mark would be lost.
    await prose
      .locator(":scope > div")
      .first()
      .locator("p")
      .evaluate((el) => {
        el.setAttribute("data-kept", "yes");
      });
    await push(page, "token", { t: "paragraph, still being written" });
    await expect(prose.locator(":scope > div").nth(1)).toContainText("still being written");
    await expect(prose.locator('[data-kept="yes"]')).toHaveCount(1);
    // The caret is inside the block being written, at the end of its text.
    await expect(prose.locator(":scope > div").nth(1).locator("p .caret")).toHaveCount(1);
    // New words fade in, and the fade is not restarted by the next rebuild.
    await expect(prose.locator(".fresh").first()).toBeAttached();
  });

  test("words written after a card start a part of their own, below it", async ({ page }) => {
    await ask(page, "what time is it in Tokyo?");
    await push(page, "conversation", CONVERSATION);
    await reply(page, 1, { say: "Checking the clock.", called: "Clock" });
    const prose = answerOf(page).locator(".prose");
    await expect(prose).toHaveText("Checking the clock.");
    // The caret is where the writing is: here, until the call.
    await expect(prose.locator(".caret")).toHaveCount(1);
    await called(page, 0, {
      label: "Clock",
      tool: "clock",
      card: "clock",
      data: { time: "09:30", date: "Tuesday", zone: "Asia/Tokyo" },
    });
    // The words before the call are finished; nothing is being written.
    await expect(prose.locator(".caret")).toHaveCount(0);
    expect(await order(page)).toEqual(["text", "cards"]);

    await push(page, "step", { id: "reply-2", kind: "model", status: "running", label: "Writing" });
    await push(page, "start", START);
    await push(page, "token", { t: "It is half past ", ttft_ms: 70 });
    await push(page, "token", { t: "nine." });
    await expect(prose).toHaveCount(2);
    await expect(prose.nth(1)).toContainText("It is half past nine.");
    // The first part kept what it was; the caret moved to the new one.
    await expect(prose.first()).toHaveText("Checking the clock.");
    await expect(prose.nth(1).locator(".caret")).toHaveCount(1);
    expect(await order(page)).toEqual(["text", "cards", "text"]);
    const below = await answerOf(page).evaluate((el) => {
      const card = el.querySelector('[data-part="cards"]')?.getBoundingClientRect();
      const after = el.querySelectorAll(".prose")[1]?.getBoundingClientRect();
      return Boolean(card && after && card.bottom <= after.top);
    });
    expect(below).toBe(true);
    await push(page, "done", DONE);
    await end(page);
    await expect(prose.nth(1)).toHaveText("It is half past nine.");
    await expect(prose.locator(".caret")).toHaveCount(0);
  });

  test("the thread follows the answer only while the reader is at the bottom", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 520 });
    await ask(page, "write a lot");
    await push(page, "conversation", CONVERSATION);
    await push(page, "start", START);
    const wrap = page.locator("#stream-wrap");
    const paragraph = (n: number) =>
      `Paragraph ${n} of a long answer that goes on for a while.\n\n`;
    for (let n = 0; n < 12; n++)
      await push(page, "token", n ? { t: paragraph(n) } : { t: paragraph(n), ttft_ms: 80 });
    await expect(answerOf(page).locator(".prose")).toContainText("Paragraph 11");
    const atBottom = () =>
      wrap.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 2);
    await expect.poll(atBottom).toBe(true);

    // Scrolled back to read: more text must not pull the page down.
    await wrap.evaluate((el) => {
      el.scrollTop = 0;
    });
    for (let n = 12; n < 18; n++) await push(page, "token", { t: paragraph(n) });
    await expect(answerOf(page).locator(".prose")).toContainText("Paragraph 17");
    expect(await wrap.evaluate((el) => el.scrollTop)).toBe(0);
  });
});
