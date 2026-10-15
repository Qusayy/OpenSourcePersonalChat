import { expect, test } from "@playwright/test";
import { reseed } from "../support";
import {
  answer,
  answerOf,
  ask,
  CONVERSATION,
  fakeChat,
  openStored,
  posts,
  push,
  question,
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

  test("its tool results are drawn above the answer they informed", async ({ page }) => {
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
