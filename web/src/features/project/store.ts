/* The project of the conversation on the sheet: its files as they stand, the
 * one being shown, which of its folders are closed, and whether the panel is
 * open.
 *
 * Files arrive two ways. A conversation opened from history brings its
 * project from the server (`loadProject`). An answer being written says each
 * file as it goes (`fileEvent`): it opens, its lines arrive, it closes with
 * the version it was kept as; a call moves one or removes one. Lines come
 * many to a second, so they are held here and put in the store on the page's
 * own paint tick (`flushProject`), not one render a line.
 *
 * Every folder is open until it is closed. Which are closed is remembered for
 * each conversation in this browser, where the browser lets it be; without
 * storage the panel works the same and simply forgets.
 */

import { create } from "zustand";
import { api } from "@/lib/api";
import type { FileEventData, ProjectFileRow } from "@/lib/types";
import { buildTree, byName, folderPaths, foldersAbove, treeOrder } from "./tree";

export interface ProjectFile {
  path: string;
  lang: string;
  content: string;
  lines: number;
  /** The version it was kept as; null while it is being written. */
  version: number | null;
  writing: boolean;
  /** Why it was not kept, where a limit stopped it. */
  why: string | null;
}

interface ProjectState {
  /** The conversation the files are of. */
  cid: string | null;
  /** By path, in path order. */
  files: ProjectFile[];
  selected: string | null;
  /** The folders that are closed, by path. Every other folder is open. */
  collapsed: ReadonlySet<string>;
  /** Shown beside the thread on a wide screen, or as a drawer on a narrow one. */
  open: boolean;
}

export const useProject = create<ProjectState>(() => ({
  cid: null,
  files: [],
  selected: null,
  collapsed: new Set(),
  open: false,
}));

const set = useProject.setState;
const get = useProject.getState;

// Lines that have arrived since the last paint, by path.
const pending = new Map<string, string>();

const byPath = (a: ProjectFile, b: ProjectFile) => byName(a.path, b.path);
const countLines = (text: string) =>
  text ? text.split("\n").length - (text.endsWith("\n") ? 1 : 0) : 0;

function upsert(files: ProjectFile[], file: ProjectFile): ProjectFile[] {
  const rest = files.filter((f) => f.path !== file.path);
  return [...rest, file].sort(byPath);
}

/** The first file the tree lists: what is shown when nothing was chosen. */
export function firstFile(files: readonly ProjectFile[]): string | null {
  return treeOrder(buildTree(files))[0] ?? null;
}

/* ------------------------------------------------------ closed folders -- */

/** Where a conversation's closed folders are remembered. */
export const collapsedKey = (cid: string): string => `aurora.project.closed.${cid}`;

function remembered(cid: string | null): Set<string> {
  if (!cid) return new Set();
  try {
    const kept: unknown = JSON.parse(localStorage.getItem(collapsedKey(cid)) ?? "[]");
    return new Set(Array.isArray(kept) ? kept.filter((p) => typeof p === "string") : []);
  } catch {
    return new Set(); // no storage, or something in it that is not ours
  }
}

function remember(cid: string | null, collapsed: ReadonlySet<string>): void {
  if (!cid) return;
  try {
    if (collapsed.size) localStorage.setItem(collapsedKey(cid), JSON.stringify([...collapsed]));
    else localStorage.removeItem(collapsedKey(cid));
  } catch {
    /* storage refused: the panel still works, it just forgets */
  }
}

/** Close or open folders, and remember it. */
function setCollapsed(collapsed: ReadonlySet<string>): void {
  if (collapsed === get().collapsed) return;
  set({ collapsed });
  remember(get().cid, collapsed);
}

/** The same folders closed, but for every one `path` is in. The same set
 *  when none of them was closed. */
function openedAbove(collapsed: ReadonlySet<string>, path: string | null): ReadonlySet<string> {
  const above = path ? foldersAbove(path).filter((f) => collapsed.has(f)) : [];
  if (!above.length) return collapsed;
  const next = new Set(collapsed);
  for (const folder of above) next.delete(folder);
  return next;
}

/** Only the closed folders the files still have. */
function stillThere(collapsed: ReadonlySet<string>, files: readonly ProjectFile[]) {
  if (!collapsed.size) return collapsed;
  const folders = new Set(folderPaths(buildTree(files)));
  const kept = [...collapsed].filter((f) => folders.has(f));
  return kept.length === collapsed.size ? collapsed : new Set(kept);
}

/** Open a folder that is closed, or close one that is open. */
export function toggleFolder(path: string): void {
  const next = new Set(get().collapsed);
  if (!next.delete(path)) next.add(path);
  setCollapsed(next);
}

export function expandAll(): void {
  if (get().collapsed.size) setCollapsed(new Set());
}

export function collapseAll(): void {
  setCollapsed(new Set(folderPaths(buildTree(get().files))));
}

/* --------------------------------------------------------------- files -- */

/** Start over: a new conversation, or none. Its closed folders are the ones
 *  it was left with. */
export function resetProject(cid: string | null = null): void {
  pending.clear();
  set({ cid, files: [], selected: null, collapsed: remembered(cid), open: false });
}

/** The project of a conversation, from the server: a stored one being
 *  opened, or the one on the sheet once an answer has ended, which puts each
 *  file as it was kept in place of what the stream showed. What is shown and
 *  whether the panel is open stay as they were. */
export async function loadProject(cid: string): Promise<void> {
  if (get().cid !== cid) resetProject(cid);
  let rows: ProjectFileRow[];
  try {
    rows = await api.project(cid);
  } catch {
    return; // no project to show; the conversation itself still opens
  }
  if (get().cid !== cid) return;
  const files = rows
    .map((r) => ({
      path: r.path,
      lang: r.lang,
      content: r.content,
      lines: r.lines,
      version: r.version,
      writing: false,
      why: null,
    }))
    .sort(byPath);
  const selected = get().selected;
  set({
    files,
    selected: rows.some((r) => r.path === selected) ? selected : firstFile(files),
  });
}

/** The conversation a stream is in. A new one starts a new project. */
export function projectConversation(cid: string): void {
  if (get().cid !== cid) resetProject(cid);
}

/** One file event of the stream. Returns true when a file opened, which is
 *  when the panel offers itself. */
export function fileEvent(event: FileEventData): boolean {
  if (event.type === "line") {
    pending.set(event.path, (pending.get(event.path) ?? "") + event.text);
    return false;
  }
  flushProject();
  if (event.type === "open") {
    set((s) => ({
      files: upsert(s.files, {
        path: event.path,
        lang: event.lang,
        content: "",
        lines: 0,
        version: null,
        writing: true,
        why: null,
      }),
      selected: event.path,
    }));
    // The file being written is shown, and so is where it is.
    setCollapsed(openedAbove(get().collapsed, event.path));
    return true;
  }
  if (event.type === "move") {
    const s = get();
    const was = s.files.find((f) => f.path === event.from);
    if (!was) return false;
    const moved: ProjectFile = {
      ...was,
      path: event.to,
      version: event.version ?? was.version,
      lang: event.lang ?? was.lang,
    };
    const files = upsert(
      s.files.filter((f) => f.path !== event.from),
      moved,
    );
    // The file shown goes where it went; with none shown, it is the one.
    const follows = s.selected === event.from || s.selected === null;
    set({ files, selected: follows ? event.to : s.selected });
    setCollapsed(stillThere(openedAbove(s.collapsed, event.to), files));
    return false;
  }
  if (event.type === "remove") {
    const s = get();
    if (!s.files.some((f) => f.path === event.path)) return false;
    pending.delete(event.path);
    const files = s.files.filter((f) => f.path !== event.path);
    let selected = s.selected;
    if (selected === event.path) {
      // The file shown is gone: the next one in the tree, or the one before.
      const order = treeOrder(buildTree(s.files));
      const at = order.indexOf(event.path);
      selected = order[at + 1] ?? order[at - 1] ?? null;
    }
    set({ files, selected });
    setCollapsed(stillThere(openedAbove(s.collapsed, selected), files));
    return false;
  }
  set((s) => ({
    files: s.files.map((f) =>
      f.path === event.path
        ? {
            ...f,
            writing: false,
            lines: event.lines,
            version: event.kept === false ? f.version : (event.version ?? f.version),
            why: event.kept === false ? (event.why ?? "It was not kept.") : null,
          }
        : f,
    ),
  }));
  return false;
}

/** Put the lines that have arrived into the files they belong to. */
export function flushProject(): void {
  if (!pending.size) return;
  const arrived = new Map(pending);
  pending.clear();
  set((s) => ({
    files: s.files.map((f) => {
      const more = arrived.get(f.path);
      if (more === undefined) return f;
      const content = f.content + more;
      return { ...f, content, lines: countLines(content) };
    }),
  }));
}

/** Show one file, and the panel with it, with every folder it is in open. */
export function openFile(path: string): void {
  set({ selected: path, open: true });
  setCollapsed(openedAbove(get().collapsed, path));
}

export function setProjectOpen(open: boolean): void {
  set({ open });
}
