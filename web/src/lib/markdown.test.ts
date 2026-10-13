// @vitest-environment node
import { describe, expect, it } from "vitest";
import { escapeHtml, renderMarkdown, splitBlocks } from "./markdown";

const COPY = '<button class="copy-btn" type="button" data-copy>Copy</button>';
const block = (label: string, escaped: string) =>
  `<div class="codeblock" data-code="${escaped}"><header><span>${label}</span>${COPY}</header>` +
  `<pre><code>${escaped}</code></pre></div>`;

describe("escapeHtml", () => {
  it("escapes the five characters that can start markup or end an attribute", () => {
    expect(escapeHtml(`<a href="x" title='y'>&</a>`)).toBe(
      "&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;",
    );
  });

  it("takes anything, as text", () => {
    expect(escapeHtml(12)).toBe("12");
    expect(escapeHtml(null)).toBe("null");
    expect(escapeHtml(undefined)).toBe("undefined");
  });
});

describe("renderMarkdown: nothing the model writes becomes markup", () => {
  it("escapes a script tag, an ampersand and both quotes in a paragraph", () => {
    expect(renderMarkdown(`<script>alert(1)</script> & "q" 'a'`)).toBe(
      "<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot; &#39;a&#39;</p>",
    );
  });

  it("escapes inside every construct that carries text", () => {
    const evil = "<img src=x onerror=alert(1)>";
    const safe = "&lt;img src=x onerror=alert(1)&gt;";
    for (const src of [
      `# ${evil}`,
      `- ${evil}`,
      `1. ${evil}`,
      `> ${evil}`,
      `**${evil}**`,
      `\`${evil}\``,
      `| ${evil} | b |\n|---|---|\n| ${evil} | 2 |`,
      `\`\`\`${"\n"}${evil}\n\`\`\``,
    ]) {
      const html = renderMarkdown(src);
      expect(html).not.toContain("<img");
      expect(html).toContain(safe);
    }
  });

  it("cannot be made to close an attribute from a link or a code fence", () => {
    expect(renderMarkdown('[x](https://a.b/"onmouseover="alert(1))')).toBe(
      '<p><a href="https://a.b/&quot;onmouseover=&quot;alert(1" target="_blank" rel="noopener noreferrer">x</a>)</p>',
    );
    expect(renderMarkdown('```\n"><script>x</script>\n```')).toBe(
      block("text", "&quot;&gt;&lt;script&gt;x&lt;/script&gt;"),
    );
  });

  it("labels a fence only with a plain language name", () => {
    // Anything else on the opening line means it is not read as a fence at all.
    const html = renderMarkdown('```"><b>\ncode\n```');
    expect(html).not.toContain("<b>");
    expect(html).toContain("&lt;b&gt;");
  });
});

describe("renderMarkdown: inline", () => {
  it("renders strong, emphasis and strikethrough", () => {
    expect(renderMarkdown("**bold** and *it* and _it_ and __bold__ and ~~gone~~")).toBe(
      "<p><strong>bold</strong> and <em>it</em> and <em>it</em> and <strong>bold</strong> and <del>gone</del></p>",
    );
  });

  it("leaves an unclosed mark as it was typed, which is what a stream looks like mid-word", () => {
    expect(renderMarkdown("**bol")).toBe("<p>**bol</p>");
    expect(renderMarkdown("2 * 3 * 4")).toBe("<p>2 * 3 * 4</p>");
  });

  it("keeps code spans out of every other rule", () => {
    expect(renderMarkdown("use `a*b*c` and `__init__` here")).toBe(
      "<p>use <code>a*b*c</code> and <code>__init__</code> here</p>",
    );
    expect(renderMarkdown("`<b>`")).toBe("<p><code>&lt;b&gt;</code></p>");
  });

  it("numbers code spans within one run of text", () => {
    expect(renderMarkdown("`a` then `b` then `c`")).toBe(
      "<p><code>a</code> then <code>b</code> then <code>c</code></p>",
    );
  });

  it("turns a newline inside a paragraph into a line break", () => {
    expect(renderMarkdown("one\ntwo")).toBe("<p>one<br>two</p>");
  });
});

describe("renderMarkdown: links", () => {
  it("links http and https, opening elsewhere", () => {
    expect(renderMarkdown("[site](https://example.com/a?b=1&c=2)")).toBe(
      '<p><a href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">site</a></p>',
    );
    expect(renderMarkdown("[plain](http://example.com)")).toContain('href="http://example.com"');
  });

  it("does not link any other scheme: the text stays text", () => {
    for (const url of [
      "javascript:alert(1)",
      "data:text/html,x",
      "vbscript:x",
      "/relative",
      "//host/x",
    ]) {
      const html = renderMarkdown(`[x](${url})`);
      expect(html).not.toContain("<a");
      expect(html).toBe(`<p>[x](${url})</p>`);
    }
  });
});

describe("renderMarkdown: code blocks", () => {
  it("renders a fence with its language and the text twice: to show and to copy", () => {
    expect(renderMarkdown("```js\nconst a = 1;\n```")).toBe(block("js", "const a = 1;"));
  });

  it("calls a fence with no language 'text'", () => {
    expect(renderMarkdown("```\nplain\n```")).toBe(block("text", "plain"));
  });

  it("leaves markdown inside a fence alone", () => {
    expect(
      renderMarkdown("```md\n# not a heading\n- not a list\n**not bold** `not a span`\n```"),
    ).toBe(block("md", "# not a heading\n- not a list\n**not bold** `not a span`"));
  });

  it("does not end a fence at a run of backticks in the middle of a line", () => {
    expect(renderMarkdown("```\nrun: ``` in the middle\nnext\n```")).toBe(
      block("text", "run: ``` in the middle\nnext"),
    );
  });

  it("keeps blank lines inside a fence", () => {
    expect(renderMarkdown("```\na\n\nb\n```")).toBe(block("text", "a\n\nb"));
  });

  it("renders a fence that has not been closed yet, as it is mid-stream", () => {
    expect(renderMarkdown("text\n\n```py\nprint(1)")).toBe(`<p>text</p>${block("py", "print(1)")}`);
    expect(renderMarkdown("```py")).toBe(block("py", ""));
  });

  it("carries on after a closed fence", () => {
    expect(renderMarkdown("```\na\n```\nafter")).toBe(`${block("text", "a")}<p>after</p>`);
  });
});

describe("renderMarkdown: blocks", () => {
  it("moves headings one level down and stops at h4: h1 belongs to the page", () => {
    expect(renderMarkdown("# One")).toBe("<h2>One</h2>");
    expect(renderMarkdown("## Two")).toBe("<h3>Two</h3>");
    expect(renderMarkdown("### Three")).toBe("<h4>Three</h4>");
    expect(renderMarkdown("#### Four")).toBe("<h4>Four</h4>");
    expect(renderMarkdown("###### Six")).toBe("<h4>Six</h4>");
  });

  it("needs a space after the hashes", () => {
    expect(renderMarkdown("#hashtag")).toBe("<p>#hashtag</p>");
  });

  it("renders inline marks in a heading", () => {
    expect(renderMarkdown("## The `main` loop")).toBe("<h3>The <code>main</code> loop</h3>");
  });

  it("renders bulleted and numbered lists", () => {
    expect(renderMarkdown("- a\n- b\n* c\n+ d")).toBe(
      "<ul><li>a</li><li>b</li><li>c</li><li>d</li></ul>",
    );
    expect(renderMarkdown("1. a\n2) b")).toBe("<ol><li>a</li><li>b</li></ol>");
  });

  it("lets the first item decide the kind of list", () => {
    expect(renderMarkdown("- a\n1. b")).toBe("<ul><li>a</li><li>b</li></ul>");
  });

  it("flattens an indented item into its parent's list: nesting is not supported", () => {
    expect(renderMarkdown("- a\n  - b\n- c")).toBe("<ul><li>a</li><li>b</li><li>c</li></ul>");
  });

  it("joins an indented continuation line to its item", () => {
    expect(renderMarkdown("- a\n  more of a\n- b")).toBe(
      "<ul><li>a\nmore of a</li><li>b</li></ul>",
    );
  });

  it("ends a list at a line that is neither an item nor indented", () => {
    expect(renderMarkdown("- a\nafter")).toBe("<ul><li>a</li></ul><p>after</p>");
  });

  it("renders a rule", () => {
    expect(renderMarkdown("above\n\n---\n\nbelow")).toBe("<p>above</p><hr><p>below</p>");
    expect(renderMarkdown("***")).toBe("<hr>");
  });

  it("renders a quotation as markdown of its own", () => {
    expect(renderMarkdown("> quoted\n> more\n\nafter")).toBe(
      "<blockquote><p>quoted<br>more</p></blockquote><p>after</p>",
    );
    expect(renderMarkdown("> - a\n> - b")).toBe(
      "<blockquote><ul><li>a</li><li>b</li></ul></blockquote>",
    );
  });

  it("renders a table from a header row and a separator row", () => {
    expect(renderMarkdown("| a | b |\n|---|:-:|\n| 1 | **2** |\n| 3 |")).toBe(
      "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody>" +
        "<tr><td>1</td><td><strong>2</strong></td></tr><tr><td>3</td><td></td></tr></tbody></table>",
    );
  });

  it("does not take a line with a pipe for a table without the separator", () => {
    expect(renderMarkdown("a | b")).toBe("<p>a | b</p>");
  });

  it("separates paragraphs at blank lines and normalises line endings", () => {
    expect(renderMarkdown("one\r\n\r\ntwo\rthree")).toBe("<p>one</p><p>two<br>three</p>");
  });

  it("renders nothing from nothing", () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown(null)).toBe("");
    expect(renderMarkdown(undefined)).toBe("");
    expect(renderMarkdown("\n\n  \n")).toBe("");
  });
});

describe("renderMarkdown: the code-span placeholder", () => {
  it("never leaves its bytes in the output", () => {
    const html = renderMarkdown(
      "# `h` and `i`\n\n- `a` **b** `c`\n\n| `x` | y |\n|---|---|\n| `1` | `2` |\n\n> `q`\n\ntext `t`",
    );
    expect(html).not.toContain("\u0000");
    expect(html.match(/<code>/g)).toHaveLength(9);
  });

  // Pinned rather than endorsed: text that already contains the placeholder is
  // read as one, and there is no span behind it. Nothing unescaped comes of it.
  it("reads a placeholder that was in the text as a span that is not there", () => {
    expect(renderMarkdown("a\u00000\u0000b")).toBe("<p>a<code>undefined</code>b</p>");
  });
});

describe("splitBlocks", () => {
  it("splits at blank lines", () => {
    expect(splitBlocks("one\n\ntwo\n\n\nthree")).toEqual(["one", "two", "three"]);
  });

  it("keeps lines that are only separated by a newline together", () => {
    expect(splitBlocks("- a\n- b\ntext")).toEqual(["- a\n- b\ntext"]);
  });

  it("does not split inside a fence, closed or not", () => {
    expect(splitBlocks("before\n\n```js\na\n\nb\n```\n\nafter")).toEqual([
      "before",
      "```js\na\n\nb\n```",
      "after",
    ]);
    expect(splitBlocks("before\n\n```js\na\n\nb")).toEqual(["before", "```js\na\n\nb"]);
  });

  it("treats a line of spaces as blank and normalises line endings", () => {
    expect(splitBlocks("one\r\n   \r\ntwo")).toEqual(["one", "two"]);
  });

  it("returns no blocks for no text, and no trailing empty block", () => {
    expect(splitBlocks("")).toEqual([]);
    expect(splitBlocks(null)).toEqual([]);
    expect(splitBlocks(undefined)).toEqual([]);
    expect(splitBlocks("one\n\n")).toEqual(["one"]);
  });

  it("gives blocks that render to the same thing as the whole", () => {
    const src =
      "# Title\n\nA paragraph\nof two lines.\n\n- a\n- b\n\n```py\nx = 1\n\ny = 2\n```\n\nEnd.";
    expect(splitBlocks(src).map(renderMarkdown).join("")).toBe(renderMarkdown(src));
  });
});
