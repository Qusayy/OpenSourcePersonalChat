import { useEffect, useLayoutEffect, useRef } from "react";
import { Outlet, useLocation } from "react-router";
import { mountAurora } from "@/lib/aurora";
import { installCopyButtons } from "@/lib/clipboard";
import { curtains, mountCurtains } from "@/lib/curtains";
import { cx } from "@/lib/cx";
import type { EngineStatus } from "@/lib/types";
import { useInfo, usePollHealth, usePollTelemetry } from "@/state/queries";
import { toast, useShell } from "@/state/shell";
import { type PageName, usePage } from "./page";
import { StatusLine } from "./StatusLine";
import { TopBar } from "./TopBar";

/* The frame every page shares: three rows and nothing beside them — a top
   bar, the sheet, and the status line. There is no sidebar and no metrics
   column: history opens on demand, and every fact about the machine lives
   once, in the status line.

   The rows are the page grid itself (see `body` in styles/base.css): #root
   has no box of its own, so the bar, the page's <main> and the line are the
   grid's three children, exactly as they were when the server wrote them. */

export function Layout() {
  const page = usePage();
  usePollHealth();
  usePollTelemetry();
  useDocument(page);
  useHealthNotices();
  usePageChange();
  useShellSetup();

  return (
    <>
      {/* Hidden until focused, then a real, visible target — a keyboard user
          should not have to tab the whole top bar to reach the composer.
          :focus, not :focus-visible — the link is only reachable by keyboard
          or AT in the first place, and :focus-visible does not match
          programmatic focus, which is exactly how assistive technology moves
          to it. */}
      <a
        href="#main"
        className={cx(
          "fixed top-2 left-2 z-(--z-skip) py-2 px-4 rounded-1 bg-ink text-sheet text-2 no-underline",
          "[transform:translateY(-200%)] transition-[transform] duration-(--dur-2) ease-arrive",
          "focus:[transform:none] focus:outline-sheet focus:-outline-offset-4",
        )}
      >
        Skip to content
      </a>
      <Curtains page={page} />
      <AuroraStrip />
      <TopBar page={page} />
      <Outlet />
      <StatusLine page={page} />
      <Toasts />
      <Announcer />
    </>
  );
}

/* ----------------------------------------------------------- the aurora -- */

/**
 * Light rising from the status line while a request is being read or written
 * (lib/aurora.ts). Fixed to the window and behind everything, so it can never
 * cover the composer or a toast. Absent at rest: the script
 * shows and hides the element itself, so React only ever says "hidden" once.
 */
function AuroraStrip() {
  const strip = useRef<HTMLDivElement>(null);
  useEffect(() => (strip.current ? mountAurora(strip.current) : undefined), []);
  return (
    <div className="aurora" id="aurora" aria-hidden="true" hidden ref={strip}>
      <canvas />
    </div>
  );
}

/* --------------------------------------------------------- the curtains -- */

/**
 * The aurora at rest, hanging from the top bar (lib/curtains.ts). Fixed to
 * the window and behind everything, like the strip above. The long pages
 * always hang them; the chat page says when, since only its empty sheet does.
 */
function Curtains({ page }: { page: PageName }) {
  const band = useRef<HTMLDivElement>(null);
  useEffect(() => (band.current ? mountCurtains(band.current) : undefined), []);
  useEffect(() => {
    if (page !== "chat") curtains.rest(true);
  }, [page]);
  return (
    <div className="curtains" id="curtains" aria-hidden="true" hidden ref={band}>
      <canvas />
    </div>
  );
}

/* --------------------------------------------------------------- toasts -- */

function Toasts() {
  const toasts = useShell((s) => s.toasts);
  return (
    <div
      id="toasts"
      aria-live="polite"
      // --status-h is measured by the status line, which wraps to two rows at
      // middling widths; a toast must clear it either way.
      className={cx(
        "fixed inset-x-0 bottom-[calc(var(--status-h,44px)+var(--s-4))] z-(--z-toast)",
        "grid justify-items-center gap-2 px-edge pointer-events-none",
      )}
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cx(
            "max-w-[34rem] py-2 px-4 rounded-1 text-sheet shadow-pop text-2 animate-toast-in",
            t.kind === "err" ? "bg-fault" : "bg-ink",
            // No travel under reduced motion, and never an entrance that
            // leaves the message unseen.
            "motion-reduce:animate-none motion-reduce:opacity-100! motion-reduce:translate-none!",
          )}
          style={
            t.leaving
              ? { transition: "opacity 200ms, translate 200ms", opacity: 0, translate: "0 8px" }
              : undefined
          }
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------- announcements -- */

/** One deliberate announcement channel; see `announce` in state/shell.ts. */
function Announcer() {
  const message = useShell((s) => s.announcement);
  return (
    <p className="sr-only" id="sr-status" role="status" aria-live="polite">
      {message}
    </p>
  );
}

/* ---------------------------------------------------------------- hooks -- */

/** The title, and which page <html> says it is (the status line's layout
 *  depends on it). The server sets both for the first paint; this keeps them
 *  right as the page changes without a reload. */
function useDocument(page: PageName): void {
  const { model } = useInfo();
  useEffect(() => {
    document.documentElement.dataset.page = page;
    document.title =
      page === "bench"
        ? `Benchmark — ${model}`
        : page === "about"
          ? "About — Aurora"
          : `Aurora — ${model}`;
  }, [page, model]);
}

/** Say when the model becomes ready, and when it fails. */
function useHealthNotices(): void {
  const { status } = useInfo();
  const last = useRef<EngineStatus>(status);
  useEffect(() => {
    if (status === last.current) return;
    if (status === "ready" && last.current === "loading") toast("The model is ready.");
    if (status === "error") toast("The model could not be loaded", "err");
    last.current = status;
  }, [status]);
}

/**
 * What a full page load used to do for free. After a change of page: focus
 * goes to the new page's content (the chat page takes it to the composer
 * itself), and a link to a part of a page (/bench#memory) scrolls to it. Not
 * on the first render: the browser has just loaded the document and focus is
 * where it should be.
 */
function usePageChange(): void {
  const { pathname, hash } = useLocation();
  // The page last seen, not a "has run once" flag: a development build runs
  // every effect twice on mount, and the second run of a flag would read as a
  // change of page and take focus from wherever the visitor had put it.
  const seen = useRef(pathname);
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs for a new page, not for a new hash on the same one
  useEffect(() => {
    if (seen.current === pathname) return;
    seen.current = pathname;
    if (hash) return;
    const main = document.getElementById("main");
    if (main && !main.contains(document.activeElement)) main.focus({ preventScroll: true });
  }, [pathname]);
  // A layout effect, so the scroll is part of the same commit as the page: on
  // a fresh load the browser looked for the target before there was one, and
  // when the change of page is animated the new page is pictured already at
  // the target instead of jumping to it afterwards.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the same #part on another page is a new place to scroll to
  useLayoutEffect(() => {
    if (!hash || hash === "#main") return;
    let target: HTMLElement | null = null;
    try {
      target = document.getElementById(decodeURIComponent(hash.slice(1)));
    } catch {
      return; // not a well-formed fragment; nothing to go to
    }
    if (!target) return;
    target.scrollIntoView();
    // A loaded page leaves the keyboard's place at the target, so the next Tab
    // goes on from there. Without a load, that takes moving focus to it; it
    // is a place, not a control, and draws no ring (see base.css).
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.dataset.hashTarget = "";
    target.focus({ preventScroll: true });
  }, [hash, pathname]);
}

/** Things done once, for the life of the page. */
function useShellSetup(): void {
  useEffect(() => {
    const root = document.documentElement;

    // How wide a classic scrollbar is here; zero where scrollbars overlay. A
    // scroller reserves this much on both sides so its column stays centred,
    // and whatever sits beside it outside the scroller needs the same to match.
    const probe = document.createElement("div");
    probe.style.cssText = "position:absolute;top:-999px;width:80px;height:80px;overflow:scroll";
    document.body.appendChild(probe);
    root.style.setProperty("--sbw", `${probe.offsetWidth - probe.clientWidth}px`);
    probe.remove();

    // The arrival plays once. The class that starts it is set in the head
    // before first paint; taking it off afterwards is what stops it replaying
    // whenever a section comes back on screen.
    const intro = root.classList.contains("intro")
      ? setTimeout(() => root.classList.remove("intro"), 1100)
      : undefined;

    // Charts draw themselves when scrolled to (shell/reveal.ts). Elements
    // start drawn; this marks the page as able to do better than that. Paper
    // cannot be scrolled: printed, every chart is shown drawn.
    root.classList.add("reveal-ready");
    const print = () => root.classList.remove("reveal-ready");
    window.addEventListener("beforeprint", print);

    const uninstallCopy = installCopyButtons(() =>
      toast("Clipboard blocked by the browser", "err"),
    );

    return () => {
      clearTimeout(intro);
      window.removeEventListener("beforeprint", print);
      uninstallCopy();
    };
  }, []);
}
