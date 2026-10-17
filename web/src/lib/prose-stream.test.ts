import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type ProseModule = typeof import("./prose-stream");

let clock = 0;

async function world({ reduce = false } = {}) {
  vi.resetModules();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: query.includes("reduced-motion") ? reduce : false })),
  );
  const mod: ProseModule = await import("./prose-stream");
  const host = document.createElement("div");
  host.className = "prose";
  document.body.append(host);
  const prose = mod.proseRenderer(host);
  const [stable, tail] = [...host.children] as [HTMLElement, HTMLElement];
  /** The stretches still fading up, as their text and how far along each is. */
  const fresh = (root: Element = host) =>
    [...root.querySelectorAll<HTMLElement>(".fresh")].map((s) => [
      s.textContent,
      s.style.animationDelay,
    ]);
  return { ...mod, host, prose, stable, tail, fresh };
}

beforeEach(() => {
  clock = 5000;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("caretHost", () => {
  const host = (html: string) => {
    const el = document.createElement("div");
    el.innerHTML = html;
    return el;
  };

  it("is the element itself when it has nothing in it", async () => {
    const { caretHost } = await world();
    const el = host("");
    expect(caretHost(el)).toBe(el);
  });

  it("goes down through the last block to where its text ends", async () => {
    const { caretHost } = await world();
    expect(caretHost(host("<p>one</p><p>two</p>")).outerHTML).toBe("<p>two</p>");
    expect(caretHost(host("<ul><li>a</li><li>b</li></ul>")).outerHTML).toBe("<li>b</li>");
    expect(caretHost(host("<blockquote><p>q</p></blockquote>")).outerHTML).toBe("<p>q</p>");
    expect(caretHost(host("<h3>Title</h3>")).outerHTML).toBe("<h3>Title</h3>");
    expect(
      caretHost(
        host(
          '<div class="codeblock"><header><span>js</span></header><pre><code>x</code></pre></div>',
        ),
      ).outerHTML,
    ).toBe("<code>x</code>");
    expect(
      caretHost(
        host(
          "<table><thead><tr><th>a</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>",
        ),
      ).outerHTML,
    ).toBe("<td>2</td>");
  });

  it("stops at inline markup: the caret follows it rather than going inside", async () => {
    const { caretHost } = await world();
    expect(caretHost(host("<p>a <strong>b</strong></p>")).tagName).toBe("P");
    expect(caretHost(host('<p>a <span class="fresh">b</span></p>')).tagName).toBe("P");
    expect(caretHost(host("<p>a <code>b</code></p>")).tagName).toBe("CODE"); // as the page has always done
  });
});

describe("inkIn and inkDry", () => {
  const el = (html: string) => {
    const div = document.createElement("div");
    div.innerHTML = html;
    return div;
  };

  it("wraps a stretch of text in a span whose fade is already as far along as the text is old", async () => {
    const { inkIn } = await world();
    const root = el("<p>hello world</p>");
    inkIn(root, [{ start: 6, end: 11, at: 900 }], 1000);
    expect(root.innerHTML).toBe(
      '<p>hello <span class="fresh" style="animation-delay: -100ms;">world</span></p>',
    );
  });

  it("counts offsets through the rendered text, across elements", async () => {
    const { inkIn } = await world();
    const root = el("<p>ab<strong>cd</strong>ef</p>");
    inkIn(root, [{ start: 1, end: 5, at: 1000 }], 1000);
    expect([...root.querySelectorAll(".fresh")].map((s) => s.textContent)).toEqual([
      "b",
      "cd",
      "e",
    ]);
    expect(root.textContent).toBe("abcdef");
    expect(root.querySelector("strong")?.textContent).toBe("cd");
  });

  it("wraps several stretches of one text node, each with its own age", async () => {
    const { inkIn } = await world();
    const root = el("<p>one two three</p>");
    inkIn(
      root,
      [
        { start: 0, end: 4, at: 800 },
        { start: 4, end: 8, at: 900 },
        { start: 8, end: 13, at: 1000 },
      ],
      1000,
    );
    expect(
      [...root.querySelectorAll<HTMLElement>(".fresh")].map((s) => [
        s.textContent,
        s.style.animationDelay,
      ]),
    ).toEqual([
      ["one ", "-200ms"],
      ["two ", "-100ms"],
      ["three", "0ms"],
    ]);
    expect(root.textContent).toBe("one two three");
  });

  it("counts white space between blocks but never wraps it", async () => {
    const { inkIn } = await world();
    const root = el("<ul>\n<li>a</li>\n<li>b</li>\n</ul>");
    inkIn(root, [{ start: 0, end: 99, at: 1000 }], 1000);
    const ul = root.firstElementChild as Element;
    expect([...ul.children].map((c) => c.tagName)).toEqual(["LI", "LI"]);
    expect([...root.querySelectorAll(".fresh")].map((s) => s.textContent)).toEqual(["a", "b"]);
  });

  it("does nothing when there is nothing fading", async () => {
    const { inkIn } = await world();
    const root = el("<p>hello</p>");
    inkIn(root, [], 1000);
    expect(root.innerHTML).toBe("<p>hello</p>");
  });

  it("inkDry takes the wrappers out and leaves the text as one node again", async () => {
    const { inkIn, inkDry } = await world();
    const before = "<p>ab<strong>cd</strong>ef</p><ul><li>one two</li></ul>";
    const root = el(before);
    inkIn(
      root,
      [
        { start: 1, end: 5, at: 950 },
        { start: 7, end: 11, at: 1000 },
      ],
      1000,
    );
    expect(root.querySelectorAll(".fresh").length).toBeGreaterThan(0);
    inkDry(root);
    expect(root.innerHTML).toBe(before);
    expect(root.querySelector("li")?.childNodes).toHaveLength(1);
  });
});

describe("proseRenderer: while the answer is being written", () => {
  it("puts two containers in the element it is given: what is settled, and the block being written", async () => {
    const { host, stable, tail } = await world();
    expect(host.children).toHaveLength(2);
    expect(stable.tagName).toBe("DIV");
    expect(tail.tagName).toBe("DIV");
  });

  it("renders the block being written, new text fading up, with the caret at the end of its text", async () => {
    const { prose, stable, tail, fresh } = await world();
    prose.update("Hello", true);
    expect(stable.innerHTML).toBe("");
    expect(tail.innerHTML).toBe(
      '<p><span class="fresh" style="animation-delay: 0ms;">Hello</span><span class="caret"></span></p>',
    );
    expect(fresh()).toEqual([["Hello", "0ms"]]);
  });

  it("rebuilds that block each tick and carries each stretch's fade across the rebuild", async () => {
    const { prose, tail, fresh } = await world();
    prose.update("Hello", true);
    const first = tail.firstElementChild;
    clock += 100;
    prose.update("Hello wor", true);
    expect(tail.firstElementChild).not.toBe(first); // rebuilt
    expect(fresh()).toEqual([
      ["Hello", "-100ms"],
      [" wor", "0ms"],
    ]);
    clock += 100;
    prose.update("Hello world", true);
    expect(fresh()).toEqual([
      ["Hello", "-200ms"],
      [" wor", "-100ms"],
      ["ld", "0ms"],
    ]);
    expect(tail.textContent).toBe("Hello world");
    expect(tail.querySelector("p")?.lastElementChild?.className).toBe("caret");
  });

  it("stops wrapping a stretch once its fade is over", async () => {
    const { prose, tail, fresh } = await world();
    prose.update("Hello", true);
    clock += 280;
    prose.update("Hello there", true);
    expect(fresh()).toEqual([[" there", "0ms"]]);
    clock += 280;
    prose.update("Hello there", true); // a tick with nothing new
    expect(fresh()).toEqual([]);
    expect(tail.innerHTML).toBe('<p>Hello there<span class="caret"></span></p>');
  });

  it("wraps nothing on the tick where markdown re-reads the block and it gets shorter", async () => {
    const { prose, tail, fresh } = await world();
    prose.update("**bold", true);
    expect(tail.textContent).toBe("**bold");
    clock += 100;
    prose.update("**bold**", true);
    expect(tail.innerHTML).toBe('<p><strong>bold</strong><span class="caret"></span></p>');
    expect(fresh()).toEqual([]);
    clock += 100;
    prose.update("**bold** then", true);
    expect(fresh()).toEqual([[" then", "0ms"]]);
  });

  it("commits a finished block once and never touches it again", async () => {
    const { prose, stable, tail } = await world();
    prose.update("First paragraph.", true);
    clock += 100;
    prose.update("First paragraph.\n\nSecond", true);
    expect(stable.children).toHaveLength(1);
    expect(stable.textContent).toBe("First paragraph.");
    expect(tail.textContent).toBe("Second");
    const settled = stable.firstElementChild;
    clock += 100;
    prose.update("First paragraph.\n\nSecond one.\n\n- a", true);
    expect(stable.firstElementChild).toBe(settled); // the same node, not a copy
    expect([...stable.children].map((c) => c.tagName)).toEqual(["P", "P"]);
    expect(tail.innerHTML).toContain("<li>");
    expect(settled?.querySelector(".caret")).toBeNull();
  });

  it("lets a block's last words finish fading after it has been committed", async () => {
    const { prose, stable, tail, fresh } = await world();
    prose.update("First", true);
    clock += 100;
    prose.update("First paragraph.\n\nSecond", true);
    // " paragraph." arrived with this tick, in a block that will not be rendered again.
    expect(fresh(stable)).toEqual([
      ["First", "-100ms"],
      [" paragraph.", "0ms"],
    ]);
    expect(fresh(tail)).toEqual([["Second", "0ms"]]);
    clock += 100;
    prose.update("First paragraph.\n\nSecond one", true);
    // The new block's offsets start from its own beginning.
    expect(fresh(tail)).toEqual([
      ["Second", "-100ms"],
      [" one", "0ms"],
    ]);
  });

  it("commits several blocks that finished within one tick", async () => {
    const { prose, stable, tail } = await world();
    prose.update("One", true);
    clock += 100;
    prose.update("One\n\n## Two\n\n```js\nlet a = 1;\n\nlet b = 2;\n```\n\nFour", true);
    expect([...stable.children].map((c) => c.tagName)).toEqual(["P", "H3", "DIV"]);
    expect(stable.querySelector("code")?.textContent).toBe("let a = 1;\n\nlet b = 2;");
    expect(tail.textContent).toBe("Four");
  });

  it("puts the caret inside whatever kind of block is being written", async () => {
    const { prose, tail } = await world();
    prose.update("- a\n- b", true);
    expect(tail.querySelector("li:last-child")?.lastElementChild?.className).toBe("caret");
    clock += 100;
    prose.update("- a\n- b\n\n```py\nprint(1)", true);
    expect(tail.querySelector("pre code")?.lastElementChild?.className).toBe("caret");
    clock += 100;
    prose.update("- a\n- b\n\n```py\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |", true);
    expect(tail.querySelector("tbody td:last-child")?.lastElementChild?.className).toBe("caret");
  });

  it("puts the caret in the empty container before any text has arrived, and takes it away when told", async () => {
    const { prose, tail, host } = await world();
    prose.update("", true);
    expect(tail.innerHTML).toBe('<span class="caret"></span>');
    prose.update("Hi", false);
    expect(host.querySelector(".caret")).toBeNull();
  });
});

describe("proseRenderer: when the answer ends", () => {
  it("renders the whole answer once, as one tree, without the caret", async () => {
    const { prose, stable, tail, renderHtml } = await withMarkdown();
    const raw = "First.\n\n- a\n- b\n\nLast";
    prose.update("First.\n\n- a\n- b\n\nLa", true);
    clock += 400;
    prose.update("First.\n\n- a\n- b\n\nLast", true);
    clock += 400;
    prose.finish(raw);
    expect(stable.innerHTML).toBe("");
    expect(tail.innerHTML).toBe(renderHtml(raw));
    expect(tail.querySelector(".caret")).toBeNull();
  });

  it("lets the last words fade up, carrying on those already fading, then leaves clean markup", async () => {
    const { prose, tail, fresh, renderHtml } = await withMarkdown();
    prose.update("First.\n\nThe last", true);
    clock += 100;
    prose.update("First.\n\nThe last wor", true); // " wor" starts fading
    clock += 50;
    prose.finish("First.\n\nThe last words."); // "ds." arrived since the last tick
    expect(fresh()).toEqual([
      ["The last", "-150ms"],
      [" wor", "-50ms"],
      ["ds.", "0ms"],
    ]);
    expect(tail.textContent).toBe("First.The last words.");
    vi.advanceTimersByTime(280 + 79);
    expect(fresh()).toHaveLength(3);
    vi.advanceTimersByTime(1);
    expect(fresh()).toEqual([]);
    expect(tail.innerHTML).toBe(renderHtml("First.\n\nThe last words."));
    expect(tail.querySelector("p:last-child")?.childNodes).toHaveLength(1);
  });

  it("does nothing the second time it is told the same answer has ended", async () => {
    const { prose, tail, fresh } = await world();
    prose.update("Almost", true);
    clock += 100;
    prose.finish("Almost done");
    const tree = tail.firstElementChild;
    const fading = fresh();
    clock += 120;
    prose.finish("Almost done");
    expect(tail.firstElementChild).toBe(tree);
    expect(fresh()).toEqual(fading); // the fade was not restarted or cut short
  });

  it("leaves no wrappers when nothing was still fading", async () => {
    const { prose, tail, fresh } = await world();
    prose.update("All of it", true);
    clock += 400;
    prose.finish("All of it");
    expect(fresh()).toEqual([]);
    expect(tail.innerHTML).toBe("<p>All of it</p>");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("leaves the text be when the whole-answer render reads it as shorter than what was showing", async () => {
    const { prose, tail, fresh } = await world();
    prose.update("**bold", true);
    clock += 50;
    prose.finish("**bold**");
    expect(tail.innerHTML).toBe("<p><strong>bold</strong></p>");
    expect(fresh()).toEqual([]);
  });

  it("renders an answer that ended before any tick painted it", async () => {
    const { prose, tail, fresh } = await world();
    prose.finish("Straight to the end.");
    expect(fresh()).toEqual([["Straight to the end.", "0ms"]]);
    vi.advanceTimersByTime(360);
    expect(tail.innerHTML).toBe("<p>Straight to the end.</p>");
  });

  it("can be written to again after it has ended", async () => {
    const { prose, stable, tail } = await world();
    prose.update("One\n\nTwo", true);
    prose.finish("One\n\nTwo");
    clock += 1000;
    prose.update("Three\n\nFour", true);
    expect(stable.textContent).toBe("Three");
    expect(tail.textContent).toBe("Four");
  });

  it("dispose cancels the tidying-up and changes nothing on screen", async () => {
    const { prose, tail, fresh } = await world();
    prose.update("Almost", true);
    clock += 100;
    prose.finish("Almost done");
    const html = tail.innerHTML;
    prose.dispose();
    expect(tail.innerHTML).toBe(html);
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(1000);
    expect(fresh()).toHaveLength(2);
  });
});

describe("proseRenderer: under reduced motion", () => {
  it("never wraps anything, and still commits blocks once", async () => {
    const { prose, stable, tail, host } = await world({ reduce: true });
    prose.update("First", true);
    clock += 100;
    prose.update("First paragraph.\n\nSecond", true);
    const settled = stable.firstElementChild;
    clock += 100;
    prose.update("First paragraph.\n\nSecond one", true);
    expect(host.querySelector(".fresh")).toBeNull();
    expect(stable.innerHTML).toBe("<p>First paragraph.</p>");
    expect(stable.firstElementChild).toBe(settled);
    expect(tail.innerHTML).toBe('<p>Second one<span class="caret"></span></p>');
  });

  it("ends in clean markup at once, with nothing left to tidy", async () => {
    const { prose, stable, tail, host } = await world({ reduce: true });
    prose.update("First.\n\nLa", true);
    clock += 50;
    prose.finish("First.\n\nLast");
    expect(stable.innerHTML).toBe("");
    expect(tail.innerHTML).toBe("<p>First.</p><p>Last</p>");
    expect(host.querySelector(".fresh")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("proseRenderer: an answer taken back", () => {
  it("leaves nothing of it, and the next answer is written from the start", async () => {
    const { prose, stable, tail } = await world({ reduce: true });
    prose.update("I cannot browse.\n\nBut generally", true);
    expect(stable.innerHTML).not.toBe("");
    prose.reset();
    expect(stable.innerHTML).toBe("");
    expect(tail.innerHTML).toBe("");
    prose.update("The motion passed.", true);
    expect(tail.innerHTML).toBe('<p>The motion passed.<span class="caret"></span></p>');
    expect(stable.innerHTML).toBe("");
  });
});

/** The renderer, and the markdown renderer it uses, from the same module graph. */
async function withMarkdown() {
  const w = await world();
  const { renderMarkdown } = await import("./markdown");
  return { ...w, renderHtml: renderMarkdown };
}
