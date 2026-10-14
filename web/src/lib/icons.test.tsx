import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { hasIcon, ICON_NAMES, Icon, iconSvg } from "./icons";

/** Markup as the browser would hold it, so two spellings of one tree compare equal. */
function parsed(html: string): Element {
  const wrap = document.createElement("div");
  wrap.innerHTML = html;
  return wrap.firstElementChild as Element;
}

/** An element as its tag, its attributes in a fixed order, and its children. */
function shape(el: Element): unknown {
  return {
    tag: el.tagName.toLowerCase(),
    attrs: Object.fromEntries(
      [...el.attributes]
        .map((a) => [a.name, a.value] as const)
        .sort(([a], [b]) => a.localeCompare(b)),
    ),
    children: [...el.children].map(shape),
  };
}

describe("iconSvg", () => {
  it("draws a 24-unit picture at 16px, hidden from assistive technology", () => {
    const svg = parsed(iconSvg("arrow"));
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg.getAttribute("class")).toBe("icon");
    expect(svg.getAttribute("width")).toBe("16");
    expect(svg.getAttribute("height")).toBe("16");
    expect(svg.getAttribute("viewBox")).toBe("0 0 24 24");
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.getAttribute("focusable")).toBe("false");
  });

  it("strokes every path in the current colour, and fills none", () => {
    const paths = [...parsed(iconSvg("calculator")).querySelectorAll("path")];
    expect(paths).toHaveLength(2);
    for (const path of paths) {
      expect(path.getAttribute("fill")).toBe("none");
      expect(path.getAttribute("stroke")).toBe("currentColor");
      expect(path.getAttribute("stroke-width")).toBe("1.8");
      expect(path.getAttribute("stroke-linecap")).toBe("round");
      expect(path.getAttribute("stroke-linejoin")).toBe("round");
    }
    expect(paths[0]!.getAttribute("d")).toBe(
      "M6 3h12a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z",
    );
  });

  it("keeps the quieter strokes quieter", () => {
    const paths = [...parsed(iconSvg("fact_check")).querySelectorAll("path")];
    expect(paths.map((p) => p.getAttribute("opacity"))).toEqual([null, null, ".6"]);
  });

  it("takes a size", () => {
    const svg = parsed(iconSvg("route", 14));
    expect(svg.getAttribute("width")).toBe("14");
    expect(svg.getAttribute("height")).toBe("14");
  });

  it("answers to the names a tool, a card and a skill each use for one picture", () => {
    for (const [alias, name] of [
      ["convert_units", "convert"],
      ["read_url", "url"],
      ["text_stats", "textstats"],
      ["exchange", "currency"],
      ["answer", "model"],
      ["calc", "calculator"],
    ]) {
      expect(iconSvg(alias)).toBe(iconSvg(name));
      expect(iconSvg(alias)).not.toBe("");
    }
  });

  it("is empty for a name with no picture", () => {
    expect(iconSvg("no_such_tool")).toBe("");
    expect(iconSvg("")).toBe("");
    expect(iconSvg(null)).toBe("");
    expect(iconSvg(undefined)).toBe("");
  });

  it("does not take an object's own machinery for a picture", () => {
    for (const name of ["constructor", "toString", "__proto__", "hasOwnProperty"]) {
      expect(hasIcon(name)).toBe(false);
      expect(iconSvg(name)).toBe("");
    }
  });
});

describe("hasIcon", () => {
  it("knows every picture and every alias", () => {
    expect(ICON_NAMES).toHaveLength(22 + 8);
    for (const name of ICON_NAMES) expect(hasIcon(name)).toBe(true);
  });

  it("says no for anything else", () => {
    expect(hasIcon("no_such_tool")).toBe(false);
    expect(hasIcon(null)).toBe(false);
    expect(hasIcon(undefined)).toBe(false);
  });
});

describe("Icon", () => {
  it("is the same drawing as the markup form, for every name", () => {
    for (const name of ICON_NAMES) {
      const fromComponent = parsed(renderToStaticMarkup(<Icon name={name} />));
      expect(shape(fromComponent)).toEqual(shape(parsed(iconSvg(name))));
    }
  });

  it("takes a size", () => {
    const svg = parsed(renderToStaticMarkup(<Icon name="error" size={14} />));
    expect(shape(svg)).toEqual(shape(parsed(iconSvg("error", 14))));
  });

  it("draws nothing for a name with no picture", () => {
    expect(renderToStaticMarkup(<Icon name="no_such_tool" />)).toBe("");
    expect(renderToStaticMarkup(<Icon name={null} />)).toBe("");
  });
});
