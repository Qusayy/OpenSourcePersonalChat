/* The files in an answer, read the way the server reads them
 * (app/projects/parse.py), and the answer as the chat shows it, with each
 * file folded into one line that opens it in the project panel.
 *
 * The two readers are held to one set of cases, in __fixtures__/file-blocks.json.
 *
 * Answers no longer write their files this way: the model writes them with
 * write_file and edit_file. The reading is kept for the answers stored
 * before then, whose files the project kept from their text, so their chips
 * still open them.
 */

const PATH = String.raw`(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+`;
const HEADER = new RegExp(
  String.raw`^\s{0,3}(?:#{1,6}\s+|[-*]\s+)?(?:\*\*|__)?\x60?(?:file:\s*)?(${PATH})\x60?(?:\*\*|__)?\s*:?\s*$`,
  "i",
);
const OPENING = new RegExp(
  String.raw`^\s{0,3}(\x60{3,})([A-Za-z0-9_+#.-]*)(?:[ \t]*[: \t][ \t]*(?:title=)?["']?(${PATH})["']?)?[ \t]*$`,
);
const CLOSING = /^\s{0,3}`{3,}\s*$/;
const SEGMENT = /^[A-Za-z0-9._-]+$/;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i;
const BARE = new Set([
  "Makefile",
  "Dockerfile",
  "Procfile",
  "LICENSE",
  "README",
  "Gemfile",
  "Rakefile",
  "Vagrantfile",
  "Jenkinsfile",
  "Caddyfile",
]);
const LANGUAGES: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  tsx: "tsx",
  jsx: "jsx",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  json: "json",
  md: "markdown",
  markdown: "markdown",
  yml: "yaml",
  yaml: "yaml",
  toml: "toml",
  ini: "ini",
  cfg: "ini",
  sh: "bash",
  bash: "bash",
  ps1: "powershell",
  sql: "sql",
  rs: "rust",
  go: "go",
  java: "java",
  kt: "kotlin",
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  cs: "csharp",
  rb: "ruby",
  php: "php",
  swift: "swift",
  lua: "lua",
  txt: "text",
  csv: "csv",
  xml: "xml",
  svg: "xml",
  env: "text",
};

/** A path as a project may hold it, or null. */
export function safePath(raw: string): string | null {
  let path = raw
    .trim()
    .replace(/^[`'"]+|[`'"]+$/g, "")
    .replace(/\\/g, "/");
  while (path.startsWith("./")) path = path.slice(2);
  if (!path || path.length > 160 || path.startsWith("/") || path.includes(":")) return null;
  const parts = path.split("/");
  if (parts.length > 12) return null;
  for (const part of parts) {
    if (part === "" || part === "." || part === "..") return null;
    if (!SEGMENT.test(part) || RESERVED.test(part) || part.endsWith(".")) return null;
  }
  const name = parts.at(-1) as string;
  if (!name.replace(/^\.+/, "").includes(".") && !BARE.has(name) && !name.startsWith("."))
    return null;
  return path;
}

/** The language a file is in: what its fence said, or what its name says. */
export function languageOf(path: string, said = ""): string {
  if (said) return said.toLowerCase();
  const name = path.split("/").at(-1) ?? "";
  if (name === "Dockerfile") return "dockerfile";
  if (name === "Makefile") return "makefile";
  const ext = name.includes(".") ? (name.split(".").at(-1) ?? "").toLowerCase() : "";
  return LANGUAGES[ext] ?? "text";
}

export type FileEvent =
  | { type: "open"; path: string; lang: string }
  | { type: "line"; path: string; text: string }
  | { type: "close"; path: string; lang: string; content: string };

interface Open {
  path: string;
  lang: string;
  fence: string;
  lines: string[];
  depth: number;
}

/** Reads an answer line by line and says what files it writes. */
export class FileBlocks {
  private partial = "";
  private pending: string | null = null;
  private file: Open | null = null;
  private other: string | null = null;

  feed(text: string): FileEvent[] {
    this.partial += text;
    const out: FileEvent[] = [];
    let at = this.partial.indexOf("\n");
    while (at >= 0) {
      out.push(...this.line(this.partial.slice(0, at), true));
      this.partial = this.partial.slice(at + 1);
      at = this.partial.indexOf("\n");
    }
    return out;
  }

  finish(): FileEvent[] {
    const last = this.partial;
    this.partial = "";
    return last ? this.line(last, false) : [];
  }

  /** The path of the file being written, if one is. */
  get writing(): string | null {
    return this.file?.path ?? null;
  }

  private line(line: string, ending: boolean): FileEvent[] {
    if (this.file) return this.inside(line, ending);
    if (this.other !== null) {
      if (CLOSING.test(line) && line.trim().startsWith(this.other)) this.other = null;
      return [];
    }
    const opened = OPENING.exec(line);
    if (opened) {
      const path = (opened[3] && safePath(opened[3])) || this.pending;
      this.pending = null;
      if (!path) {
        this.other = opened[1] as string;
        return [];
      }
      const lang = languageOf(path, opened[2] ?? "");
      this.file = { path, lang, fence: opened[1] as string, lines: [], depth: 0 };
      return [{ type: "open", path, lang }];
    }
    const header = HEADER.exec(line);
    if (header) this.pending = safePath(header[1] as string);
    else if (line.trim()) this.pending = null;
    return [];
  }

  private inside(line: string, ending: boolean): FileEvent[] {
    const f = this.file as Open;
    const bare = CLOSING.test(line) && line.trim().startsWith(f.fence);
    if (bare && f.depth === 0) {
      this.file = null;
      return [{ type: "close", path: f.path, lang: f.lang, content: f.lines.join("") }];
    }
    if (f.lang === "markdown") {
      if (bare) f.depth -= 1;
      else if (OPENING.test(line) && line.replace(/[`\s]/g, "")) f.depth += 1;
    }
    const text = line + (ending ? "\n" : "");
    f.lines.push(text);
    return [{ type: "line", path: f.path, text }];
  }
}

/** Every file an answer wrote out whole, in order. */
export function filesIn(answer: string): { path: string; lang: string; content: string }[] {
  const reader = new FileBlocks();
  const events = [...reader.feed(answer), ...reader.finish()];
  return events.flatMap((e) =>
    e.type === "close" ? [{ path: e.path, lang: e.lang, content: e.content }] : [],
  );
}

/** Marks the line a file is folded into: private-use characters no answer writes. */
export const FILE_MARK = "";

/**
 * The answer with each file it writes folded into one line,
 * `pathlinesdone|writing`, which the markdown renderer
 * draws as a chip. Everything else is left exactly as it was.
 */
export function foldFiles(raw: string): string {
  const reader = new FileBlocks();
  const out: string[] = [];
  // Lines held back because they may be a file's header.
  let held: string[] = [];
  let count = 0;
  const lines = raw.split("\n");
  lines.forEach((line, i) => {
    const last = i === lines.length - 1;
    const wasIn = reader.writing;
    const events = last ? [...reader.feed(line), ...reader.finish()] : reader.feed(`${line}\n`);
    const opened = events.find((e) => e.type === "open");
    const closed = events.find((e) => e.type === "close");
    if (opened) {
      held = [];
      count = 0;
      out.push(`${FILE_MARK}${opened.path}${FILE_MARK}0${FILE_MARK}writing`);
      return;
    }
    if (wasIn) {
      if (closed) {
        out[out.length - 1] = `${FILE_MARK}${closed.path}${FILE_MARK}${count}${FILE_MARK}done`;
        return;
      }
      count += events.filter((e) => e.type === "line").length;
      out[out.length - 1] = `${FILE_MARK}${wasIn}${FILE_MARK}${count}${FILE_MARK}writing`;
      return;
    }
    const header = HEADER.exec(line);
    if (header && safePath(header[1] as string)) {
      // A new header: whatever was held for an older one was not a file's.
      out.push(...held);
      held = [line];
      return;
    }
    if (held.length && !line.trim()) {
      held.push(line);
      return;
    }
    out.push(...held, line);
    held = [];
  });
  out.push(...held);
  return out.join("\n");
}
