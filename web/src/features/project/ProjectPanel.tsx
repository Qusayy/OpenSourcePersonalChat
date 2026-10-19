import { type KeyboardEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { projectZipUrl } from "@/lib/api";
import { cx } from "@/lib/cx";
import { Icon } from "@/lib/icons";
import { Button, buttonClass } from "@/shell/Button";
import {
  collapseAll,
  expandAll,
  firstFile,
  openFile,
  type ProjectFile,
  setProjectOpen,
  toggleFolder,
  useProject,
} from "./store";
import { buildTree, fileCount, folderPaths, type TreeRow, visibleRows } from "./tree";

/* The project of the conversation: its files as a tree of folders, the one
   being shown, and a zip of all of them.

   On a wide screen it stands beside the thread, the conversation moving over
   to make room (see `usePanelBeside`); on a narrow one it is a drawer from
   the right, like History, opened from the top bar. One component draws the
   inside of both. */

/** Wide enough to show the project beside the thread. */
export const WIDE = "(min-width: 1024px)";

export function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  useEffect(() => {
    const query = window.matchMedia(WIDE);
    const changed = () => setWide(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  return wide;
}

/** Whether the panel stands beside the thread right now. */
export function usePanelBeside(): boolean {
  const wide = useWide();
  const open = useProject((s) => s.open);
  const any = useProject((s) => s.files.length > 0);
  return wide && open && any;
}

/* -------------------------------------------------------------- the tree -- */

// Where the rows start, and how far each folder down moves them in: the
// panel's own 20px margin, then 16px a level.
const INSET = 20;
const STEP = 16;

const rowId = (row: TreeRow<ProjectFile>) =>
  `${row.node.kind === "folder" ? "folder" : "file"}:${row.node.path}`;

/**
 * The project's files as a code editor lists them: folders first, each with
 * the arrow that says whether it is open, then files, a level in for each
 * folder, with a hairline down from each open folder to its last row.
 *
 * One tree, one stop on Tab: the arrow keys move between rows, Right and
 * Left open and close folders or step in and out of them, Enter and Space
 * open a file or a folder, Home and End go to the first and last row.
 */
function ProjectTree({ files, shown }: { files: ProjectFile[]; shown: string | null }) {
  const collapsed = useProject((s) => s.collapsed);
  const tree = useMemo(() => buildTree(files), [files]);
  const rows = useMemo(() => visibleRows(tree, collapsed), [tree, collapsed]);
  const [focused, setFocused] = useState<string | null>(null);
  const els = useRef(new Map<string, HTMLElement>());
  // Set when a key moves focus: the row it moved to takes it once drawn.
  const follow = useRef(false);

  // The row Tab lands on: the one last focused, else the file shown, else the first.
  const current =
    rows.find((r) => rowId(r) === focused) ??
    rows.find((r) => r.node.kind === "file" && r.node.path === shown) ??
    rows[0];
  const currentId = current ? rowId(current) : null;

  useEffect(() => {
    if (!follow.current || !currentId) return;
    follow.current = false;
    els.current.get(currentId)?.focus();
  }, [currentId]);

  const go = (row: TreeRow<ProjectFile> | undefined) => {
    if (!row) return;
    follow.current = true;
    setFocused(rowId(row));
  };

  const onKey = (event: KeyboardEvent<HTMLElement>, at: number) => {
    const row = rows[at];
    if (!row) return;
    const { node } = row;
    switch (event.key) {
      case "ArrowDown":
        go(rows[at + 1]);
        break;
      case "ArrowUp":
        go(rows[at - 1]);
        break;
      case "Home":
        go(rows[0]);
        break;
      case "End":
        go(rows.at(-1));
        break;
      case "ArrowRight":
        if (node.kind !== "folder") break;
        if (!row.open) toggleFolder(node.path);
        else if (rows[at + 1]?.parent === node.path) go(rows[at + 1]);
        break;
      case "ArrowLeft":
        if (node.kind === "folder" && row.open) toggleFolder(node.path);
        else if (row.parent)
          go(rows.find((r) => r.node.path === row.parent && r.node.kind === "folder"));
        break;
      case "Enter":
      case " ":
        if (node.kind === "folder") toggleFolder(node.path);
        else openFile(node.path);
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  return (
    <div
      id="project-files"
      role="tree"
      aria-label="Project files"
      className="max-h-[38%] overflow-y-auto py-1 border-b border-rule"
    >
      {rows.map((row, at) => {
        const { node } = row;
        const id = rowId(row);
        const folder = node.kind === "folder";
        const file = node.kind === "file" ? node.file : null;
        const selected = !folder && node.path === shown;
        return (
          <div
            key={id}
            ref={(el) => {
              if (el) els.current.set(id, el);
              else els.current.delete(id);
            }}
            role="treeitem"
            aria-level={node.depth + 1}
            aria-setsize={row.siblings}
            aria-posinset={row.position}
            aria-expanded={folder ? row.open : undefined}
            aria-selected={folder ? undefined : selected}
            tabIndex={id === currentId ? 0 : -1}
            data-folder={folder ? node.path : undefined}
            data-path={folder ? undefined : node.path}
            onFocus={() => setFocused(id)}
            onKeyDown={(event) => onKey(event, at)}
            onClick={() => {
              setFocused(id);
              if (folder) toggleFolder(node.path);
              else openFile(node.path);
            }}
            className={cx(
              "relative flex items-center gap-2 min-h-[36px] coarse:min-h-[44px] pr-5 py-1",
              "text-2 cursor-pointer select-none hover:bg-sheet-2 focus-visible:-outline-offset-2",
              selected && "bg-sheet-2",
            )}
            style={{ paddingLeft: INSET + node.depth * STEP }}
          >
            {/* A hairline down from each folder this row is in. */}
            {Array.from({ length: node.depth }, (_, level) => (
              <span
                // biome-ignore lint/suspicious/noArrayIndexKey: one line per level, in order
                key={level}
                aria-hidden="true"
                className="tree-guide"
                style={{ left: INSET + level * STEP + 6 }}
              />
            ))}
            <span
              className="flex-none inline-flex justify-center w-3 text-ink-2"
              aria-hidden="true"
            >
              {folder ? (
                <span
                  className={cx(
                    "inline-flex transition-transform duration-(--dur-1) motion-reduce:transition-none",
                    row.open && "rotate-90",
                  )}
                >
                  <Icon name="arrow" size={12} />
                </span>
              ) : null}
            </span>
            <span
              className={cx("min-w-0 flex-1 [overflow-wrap:anywhere]", selected && "font-semibold")}
            >
              {node.name}
            </span>
            {folder ? (
              row.open ? null : (
                <span className="text-1 text-ink-2 num">{fileCount(node.count)}</span>
              )
            ) : file?.writing ? (
              <span className="project-live text-1">writing</span>
            ) : file?.why ? (
              <span className="text-1 text-fault">not kept</span>
            ) : (
              <span className="text-1 text-ink-2 num">{file?.lines}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Viewer({ file }: { file: ProjectFile | undefined }) {
  const scroller = useRef<HTMLDivElement>(null);
  // A file being written is followed to its newest line.
  // biome-ignore lint/correctness/useExhaustiveDependencies: new content is when to follow it
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && file?.writing) el.scrollTop = el.scrollHeight;
  }, [file?.content, file?.writing]);
  if (!file) return <p className="px-5 py-4 text-ink-2 text-2">No file yet.</p>;
  return (
    <div ref={scroller} id="project-viewer" className="flex-1 min-h-0 overflow-auto px-5 py-4">
      {file.why ? <p className="mt-0 mb-3 text-fault text-1">{`Not kept: ${file.why}.`}</p> : null}
      <div className="codeblock" data-code={file.content}>
        <header>
          <span>{`${file.path}${file.version ? ` · version ${file.version}` : ""}`}</span>
          <button type="button" className="copy-btn" data-copy>
            Copy
          </button>
        </header>
        <pre>
          <code>{file.content}</code>
        </pre>
      </div>
    </div>
  );
}

/** The inside of the panel, beside the thread or in the drawer. */
export function ProjectView({ onClose }: { onClose: () => void }) {
  const files = useProject((s) => s.files);
  const selected = useProject((s) => s.selected);
  const collapsed = useProject((s) => s.collapsed);
  const cid = useProject((s) => s.cid);
  const kept = files.some((f) => f.version !== null);
  const file =
    files.find((f) => f.path === selected) ?? files.find((f) => f.path === firstFile(files));
  const folders = useMemo(() => folderPaths(buildTree(files)), [files]);
  const closedAll = folders.length > 0 && folders.every((f) => collapsed.has(f));
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 pt-4 pb-3 border-b border-rule">
        <h2 id="project-label" className="text-4 font-semibold tracking-[-0.01em]">
          Project
        </h2>
        <span className="text-ink-2 text-1 num">{fileCount(files.length)}</span>
        {/* The words of the last one end on the panel's edge. */}
        <span className="flex flex-wrap items-center gap-x-1 ml-auto -mr-2">
          {folders.length ? (
            <>
              <Button
                variant="text"
                small
                id="project-expand"
                disabled={collapsed.size === 0}
                onClick={expandAll}
              >
                Expand all
              </Button>
              <Button
                variant="text"
                small
                id="project-collapse"
                disabled={closedAll}
                onClick={collapseAll}
              >
                Collapse all
              </Button>
            </>
          ) : null}
          {cid && kept ? (
            <a
              id="project-zip"
              className={buttonClass({ variant: "text" })}
              href={projectZipUrl(cid)}
              download
            >
              Download zip
            </a>
          ) : null}
          <Button variant="text" quiet id="project-close" onClick={onClose}>
            Close
          </Button>
        </span>
      </div>
      <ProjectTree files={files} shown={file?.path ?? null} />
      <Viewer file={file} />
    </div>
  );
}

/** Beside the thread, on a wide screen. */
export function ProjectPanel() {
  return (
    <aside id="project" aria-labelledby="project-label" className="project-panel">
      <ProjectView onClose={() => setProjectOpen(false)} />
    </aside>
  );
}

/** A drawer from the right, on a narrow screen. A real <dialog>, like History. */
export function ProjectDrawer() {
  const dialog = useRef<HTMLDialogElement>(null);
  const open = useProject((s) => s.open);
  const wide = useWide();
  const shown = open && !wide;
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (shown && !el.open) el.showModal();
    else if (!shown && el.open) el.close();
  }, [shown]);
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: the click is on the backdrop; the key that closes a dialog is Escape, and the platform handles it
    <dialog
      ref={dialog}
      id="project-drawer"
      className="rail project-rail"
      aria-labelledby="project-label"
      onClose={() => setProjectOpen(false)}
      onClick={(e) => {
        if (e.target === dialog.current) setProjectOpen(false);
      }}
    >
      {shown ? <ProjectView onClose={() => setProjectOpen(false)} /> : null}
    </dialog>
  );
}

/** The top bar's way to the project, where the conversation has one. */
export function ProjectButton() {
  const any = useProject((s) => s.files.length > 0);
  const open = useProject((s) => s.open);
  const writing = useProject((s) => s.files.some((f) => f.writing));
  // Beside the thread on a wide screen, the drawer on a narrow one.
  const wide = useWide();
  if (!any) return null;
  return (
    <Button
      variant="text"
      id="project-btn"
      aria-expanded={open}
      aria-controls={wide ? "project" : "project-drawer"}
      onClick={() => setProjectOpen(!open)}
    >
      Project
      {writing ? <span className="project-live ml-1" aria-hidden="true" /> : null}
    </Button>
  );
}

/** A file chip in an answer opens its file. One listener serves them all. */
export function useFileChips(): void {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const chip = (event.target as Element | null)?.closest?.("[data-file]");
      const path = chip?.getAttribute("data-file");
      if (path) openFile(path);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);
}
