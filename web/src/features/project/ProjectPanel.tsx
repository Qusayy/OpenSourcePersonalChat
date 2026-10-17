import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { projectZipUrl } from "@/lib/api";
import { cx } from "@/lib/cx";
import { Button, buttonClass } from "@/shell/Button";
import { openFile, type ProjectFile, setProjectOpen, useProject } from "./store";

/* The project of the conversation: its files, the one being shown, and a zip
   of all of them.

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

/** A path in two parts: where it is, and its name. */
function split(path: string): [string, string] {
  const at = path.lastIndexOf("/");
  return at < 0 ? ["", path] : [path.slice(0, at + 1), path.slice(at + 1)];
}

function FileList({ files, selected }: { files: ProjectFile[]; selected: string | null }) {
  return (
    <ul
      id="project-files"
      aria-label="Files"
      className="list-none m-0 p-0 max-h-[38%] overflow-y-auto border-b border-rule"
    >
      {files.map((f) => {
        const [dir, name] = split(f.path);
        const current = f.path === selected;
        return (
          <li key={f.path}>
            <button
              type="button"
              data-path={f.path}
              aria-current={current ? "true" : undefined}
              onClick={() => openFile(f.path)}
              className={cx(
                "flex items-baseline gap-2 w-full min-h-[36px] coarse:min-h-[44px] px-5 py-1",
                "text-left text-2 hover:bg-sheet-2",
                current && "bg-sheet-2",
              )}
            >
              <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
                <span className="text-ink-2">{dir}</span>
                <span className={current ? "font-semibold" : undefined}>{name}</span>
              </span>
              {f.writing ? (
                <span className="project-live text-1 text-ink-2">writing</span>
              ) : f.why ? (
                <span className="text-1 text-fault">not kept</span>
              ) : (
                <span className="text-1 text-ink-2 num">{f.lines}</span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
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
  const cid = useProject((s) => s.cid);
  const kept = files.some((f) => f.version !== null);
  const file = files.find((f) => f.path === selected) ?? files[0];
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-3 px-5 pt-4 pb-3 border-b border-rule">
        <h2 id="project-label" className="text-4 font-semibold tracking-[-0.01em]">
          Project
        </h2>
        <span className="text-ink-2 text-1 num">
          {`${files.length} file${files.length === 1 ? "" : "s"}`}
        </span>
        <span className="flex-1" />
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
      </div>
      <FileList files={files} selected={file?.path ?? null} />
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
  if (!any) return null;
  return (
    <Button
      variant="text"
      id="project-btn"
      aria-expanded={open}
      aria-controls="project"
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
