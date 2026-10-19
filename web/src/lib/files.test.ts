import { describe, expect, it } from "vitest";
import CASES from "./__fixtures__/file-blocks.json";
import { FILE_MARK, FileBlocks, filesIn, foldFiles, safePath } from "./files";
import { renderAnswer } from "./markdown";

describe("the files in an answer", () => {
  for (const c of CASES) {
    it(`reads as the server reads it: ${c.name}`, () => {
      expect(filesIn(c.answer)).toEqual(c.files);
    });
  }

  it("finds the same files however the answer arrives", () => {
    for (const c of CASES) {
      const reader = new FileBlocks();
      const events = [...c.answer].flatMap((ch) => reader.feed(ch));
      events.push(...reader.finish());
      expect(events.flatMap((e) => (e.type === "close" ? [e.content] : []))).toEqual(
        c.files.map((f) => f.content),
      );
    }
  });

  it("holds a path to a plain, relative form", () => {
    expect(safePath("./src/main.js")).toBe("src/main.js");
    expect(safePath("src\\util.py")).toBe("src/util.py");
    for (const bad of ["../x.py", "/abs.py", "C:/x.py", "con.txt", "notes", "a b.py"]) {
      expect(safePath(bad)).toBeNull();
    }
  });
});

describe("an answer as the chat shows it", () => {
  const answer = "Here.\n\n### app.py\n```python\nx = 1\ny = 2\n```\n\nRun it.";

  it("folds each file into one line, and leaves the rest as it was", () => {
    expect(foldFiles(answer)).toBe(
      `Here.\n\n${FILE_MARK}app.py${FILE_MARK}2${FILE_MARK}done\n\nRun it.`,
    );
    expect(foldFiles("Just text.\n### Not a file\nMore.")).toBe(
      "Just text.\n### Not a file\nMore.",
    );
  });

  it("folds a file still being written, with the lines so far", () => {
    expect(foldFiles("### app.py\n```python\nx = 1\ny")).toBe(
      `${FILE_MARK}app.py${FILE_MARK}2${FILE_MARK}writing`,
    );
  });

  it("draws a folded file as a chip that names it and opens it", () => {
    const html = renderAnswer(answer, { files: true });
    expect(html).toContain(
      '<button type="button" class="file-chip" data-file="app.py" data-state="done">',
    );
    expect(html).toContain("2 lines");
    expect(html).not.toContain("x = 1");
    expect(html).toContain("<p>Run it.</p>");
  });

  it("escapes a path, whatever it is", () => {
    const html = renderAnswer(`${FILE_MARK}<b>${FILE_MARK}1${FILE_MARK}done`, { files: true });
    expect(html).not.toContain("<b>");
  });

  it("folds nothing unless asked: a reply written since shows a file in its text as code", () => {
    const html = renderAnswer(answer);
    expect(html).not.toContain("file-chip");
    expect(html).toContain("x = 1");
    expect(html).toContain("<p>Run it.</p>");
  });
});
