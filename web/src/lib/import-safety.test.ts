// @vitest-environment node
import { describe, expect, it } from "vitest";

// These modules are loaded by unit tests and by the page alike. None of them
// may reach for the page when it is merely imported: no window, no document,
// no matchMedia, no timers. Here there is no page at all, so any that did
// would throw.
describe("importing a lib module where there is no DOM", () => {
  it("has no window or document to lean on", () => {
    expect(typeof window).toBe("undefined");
    expect(typeof document).toBe("undefined");
  });

  it.each([
    ["sse", () => import("./sse")],
    ["markdown", () => import("./markdown")],
    ["meter", () => import("./meter")],
    ["format", () => import("./format")],
    ["frame", () => import("./frame")],
    ["aurora", () => import("./aurora")],
    ["curtains", () => import("./curtains")],
    ["gl", () => import("./gl")],
    ["trace", () => import("./trace")],
    ["prose-stream", () => import("./prose-stream")],
    ["icons", () => import("./icons")],
    ["gate", () => import("./gate")],
  ])("%s loads", async (_name, load) => {
    await expect(load()).resolves.toBeTypeOf("object");
  });

  it("answers what does not need a page", async () => {
    const { reduced, EASE } = await import("./frame");
    expect(reduced()).toBe(false);
    expect(EASE).toContain("cubic-bezier");

    const { aurora } = await import("./aurora");
    expect(() => {
      aurora.phase("writing");
      aurora.rate(10);
      aurora.pulse();
      aurora.phase(null);
    }).not.toThrow();

    const { curtains } = await import("./curtains");
    expect(() => {
      curtains.rest(true);
      curtains.rest(false);
    }).not.toThrow();

    const { iconSvg, hasIcon } = await import("./icons");
    expect(hasIcon("clock")).toBe(true);
    expect(iconSvg("clock")).toContain("<svg");

    const { renderMarkdown } = await import("./markdown");
    expect(renderMarkdown("**ok**")).toBe("<p><strong>ok</strong></p>");
  });
});
