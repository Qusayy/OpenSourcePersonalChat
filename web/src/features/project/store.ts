/* The project of the conversation on the sheet: its files as they stand, the
 * one being shown, and whether the panel is open.
 *
 * Files arrive two ways. A conversation opened from history brings its
 * project from the server (`loadProject`). An answer being written says each
 * file as it goes (`fileEvent`): it opens, its lines arrive, it closes with
 * the version it was kept as. Lines come many to a second, so they are held
 * here and put in the store on the page's own paint tick (`flushProject`),
 * not one render a line.
 */

import { create } from "zustand";
import { api } from "@/lib/api";
import type { FileEventData, ProjectFileRow } from "@/lib/types";

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
  /** Shown beside the thread on a wide screen, or as a drawer on a narrow one. */
  open: boolean;
}

export const useProject = create<ProjectState>(() => ({
  cid: null,
  files: [],
  selected: null,
  open: false,
}));

const set = useProject.setState;
const get = useProject.getState;

// Lines that have arrived since the last paint, by path.
const pending = new Map<string, string>();

const byPath = (a: ProjectFile, b: ProjectFile) => a.path.localeCompare(b.path);
const countLines = (text: string) =>
  text ? text.split("\n").length - (text.endsWith("\n") ? 1 : 0) : 0;

function upsert(files: ProjectFile[], file: ProjectFile): ProjectFile[] {
  const rest = files.filter((f) => f.path !== file.path);
  return [...rest, file].sort(byPath);
}

/** Start over: a new conversation, or none. */
export function resetProject(cid: string | null = null): void {
  pending.clear();
  set({ cid, files: [], selected: null, open: false });
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
  set({
    files: rows
      .map((r) => ({
        path: r.path,
        lang: r.lang,
        content: r.content,
        lines: r.lines,
        version: r.version,
        writing: false,
        why: null,
      }))
      .sort(byPath),
    selected: rows.some((r) => r.path === get().selected)
      ? get().selected
      : (rows[0]?.path ?? null),
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
    return true;
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

/** Show one file, and the panel with it. */
export function openFile(path: string): void {
  set({ selected: path, open: true });
}

export function setProjectOpen(open: boolean): void {
  set({ open });
}
