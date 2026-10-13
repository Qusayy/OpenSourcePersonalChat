import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CardData, CardKind } from "@/lib/types";
import { ToolCard } from "./ToolCard";

afterEach(cleanup);

const draw = (card: CardKind, data: CardData, meta?: { label?: string; ms?: number | null }) =>
  render(<ToolCard card={card} data={data} meta={meta} />).container;

const words = (el: Element) => (el.textContent ?? "").replace(/\s+/g, " ").trim();

const FULL: Record<Exclude<CardKind, "error">, CardData> = {
  calculator: {
    expression: "(1024 * 3.5) / 7 + 2**10",
    result: 1536,
    exact: true,
    fraction: "1536/1",
  },
  weather: {
    place: "Reykjavík",
    country: "Iceland",
    condition: "Partly cloudy",
    icon: "cloud-sun",
    temperature: 7.4,
    feels_like: 3.9,
    humidity: 71,
    wind: 21.6,
    hourly: [3.2, 3.0, 4.1, 7.4],
    days: [
      { date: "2026-10-13", max: 8.1, min: 3.2, icon: "rain" },
      { date: "2026-10-14", max: 7.0, min: 2.5, icon: "snow" },
    ],
  },
  wikipedia: {
    title: "Ada Lovelace",
    description: "English mathematician",
    extract: "Augusta Ada King was a mathematician.",
    url: "https://en.wikipedia.org/wiki/Ada_Lovelace",
  },
  url: {
    url: "https://example.org/notes",
    domain: "example.org",
    title: "Notes",
    lede: "llama.cpp runs models in plain C.",
    words: 1234,
    reading_minutes: 6.2,
    truncated: true,
  },
  currency: {
    amount: 100,
    from: "EUR",
    to: "USD",
    result: 108.42,
    rate: 1.0842,
    date: "2026-10-12",
  },
  convert: { value: 26.2, from: "mi", to: "km", result_text: "42.16", dimension: "length" },
  clock: {
    time: "21:23",
    date: "Tuesday 13 October 2026",
    zone: "Asia/Tokyo",
    others: [
      { zone: "UTC", time: "12:23" },
      { zone: "America/New_York", time: "08:23" },
    ],
    note: "Unknown zone; showing UTC.",
  },
  textstats: {
    words: 108,
    sentences: 12,
    reading_minutes: 0.5,
    avg_sentence_words: 9,
    top_terms: [
      { term: "fox", count: 12 },
      { term: "dog", count: 6 },
    ],
  },
};

describe("each kind of card, with everything the tool can send", () => {
  it("calculator prints the expression and the result as given", () => {
    const el = draw("calculator", FULL.calculator, { ms: 0.4 });
    expect(words(el)).toBe("Calculatorexact<1ms(1024 * 3.5) / 7 + 2**10=15361536/1");
    expect(el.querySelector("figure")?.getAttribute("data-card")).toBe("calculator");
  });

  it("calculator says rounded when the result is not exact", () => {
    expect(words(draw("calculator", { expression: "1/3", result: 0.3333 }))).toContain("rounded");
  });

  it("weather shows now, the next day's curve and the forecast", () => {
    const el = draw("weather", FULL.weather, { ms: 412.3 });
    const text = words(el);
    expect(text).toContain("WeatherReykjavík, Iceland412ms");
    expect(text).toContain("7.4°C");
    expect(text).toContain("Partly cloudy");
    expect(text).toContain("feels 3.9° · wind 22 km/h · humidity 71%");
    expect(text).toContain("nownext 24h");
    expect(text).toContain("8°3°");
    expect(el.querySelectorAll("svg.w-art")).toHaveLength(3);
    expect(el.querySelector("svg.w-spark path.w-line")?.getAttribute("d")).toMatch(/^M0 /);
  });

  it("wikipedia links to the article and opens it apart", () => {
    const el = draw("wikipedia", FULL.wikipedia);
    const link = el.querySelector("a");
    expect(words(el)).toContain("Ada LovelaceEnglish mathematicianAugusta Ada King");
    expect(link?.getAttribute("href")).toBe("https://en.wikipedia.org/wiki/Ada_Lovelace");
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("a page that was read says how much of it there was", () => {
    const text = words(draw("url", FULL.url));
    expect(text).toContain("Page readexample.org");
    expect(text).toContain("1,234 words · about 6.2 min read · truncated");
    expect(text).toContain("Open the page");
  });

  it("currency reads as a sentence: this much, at this rate, is that much", () => {
    expect(words(draw("currency", FULL.currency))).toBe(
      "CurrencyECB 2026-10-12100.00EUR×1.0842108.42USD",
    );
  });

  it("a conversion drops the zeros it was padded with", () => {
    expect(words(draw("convert", FULL.convert))).toBe("Convertlength26.2mi42.16km");
    expect(
      words(draw("convert", { value: 1000, from: "m", to: "km", result_text: "1" })),
    ).toContain("1,000m");
  });

  it("the clock points its hands and names the other zones by city", () => {
    const el = draw("clock", FULL.clock);
    const text = words(el);
    expect(text).toContain("ClockAsia/Tokyo");
    expect(text).toContain("21:23Tuesday 13 October 2026");
    expect(text).toContain("UTC12:23New York08:23");
    expect(text).toContain("Unknown zone; showing UTC.");
    // 21:23 is 9 hours and 23 minutes round a twelve-hour face.
    expect(el.querySelector(".hour")?.getAttribute("transform")).toBe("rotate(281.5 40 40)");
    expect(el.querySelector(".minute")?.getAttribute("transform")).toBe("rotate(138.0 40 40)");
    expect(el.querySelectorAll(".ticks line")).toHaveLength(12);
  });

  it("text stats scale each term's bar to the commonest term", () => {
    const el = draw("textstats", FULL.textstats);
    expect(words(el)).toContain("108words12sentences0.5min read9.0words per sentence");
    const bars = [...el.querySelectorAll<HTMLElement>("i")].map((i) => i.style.width);
    expect(bars).toEqual(["100.0%", "50.0%"]);
  });
});

describe("a card whose tool sent nothing", () => {
  const kinds = Object.keys(FULL) as Array<keyof typeof FULL>;
  for (const kind of kinds) {
    it(`${kind} is still drawn`, () => {
      const el = draw(kind, {});
      const figure = el.querySelector("figure");
      expect(figure?.getAttribute("data-card")).toBe(kind);
      // The caption is there, and nothing in it is the word for a missing value.
      expect(words(el)).not.toMatch(/null|NaN/);
    });
  }

  it("the weather card leaves the curve out when there are too few hours", () => {
    expect(draw("weather", { hourly: [1, 2] }).querySelector(".w-spark")).toBeNull();
    expect(
      draw("weather", { hourly: [1, "x", 2, null, 3] }).querySelector(".w-spark"),
    ).not.toBeNull();
  });

  it("a condition with no drawing of its own gets a cloud", () => {
    const el = draw("weather", { icon: "partly" });
    expect(el.querySelector("svg.w-art path.w-cloud")).not.toBeNull();
  });

  it("null is tolerated where data should be", () => {
    const el = render(<ToolCard card="clock" data={null as unknown as CardData} />).container;
    expect(el.querySelector("figure")?.getAttribute("data-card")).toBe("clock");
  });
});

describe("failure", () => {
  it("a failed tool is named, and its message is read as text", () => {
    const el = draw(
      "error",
      { message: "No place called “Atlantis” was found." },
      { label: "Weather", ms: 301.2 },
    );
    expect(words(el)).toBe("Weather failed301msNo place called “Atlantis” was found.");
  });

  it("without a name or a message it still says what happened", () => {
    expect(words(draw("error", {}))).toBe("Tool failedSomething went wrong.");
  });

  it("markup in a tool's text is shown, not run", () => {
    const el = draw("wikipedia", { title: '<img src=x onerror="alert(1)">' });
    expect(el.querySelector("img")).toBeNull();
    expect(words(el)).toContain('<img src=x onerror="alert(1)">');
  });

  it("a payload that cannot be drawn is replaced by a line saying so", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const poisoned = {
      get title(): string {
        throw new Error("boom");
      },
    } as unknown as CardData;
    const { container } = render(
      <div>
        <ToolCard card="wikipedia" data={poisoned} />
        <ToolCard card="calculator" data={FULL.calculator} index={1} />
      </div>,
    );
    const figures = container.querySelectorAll("figure");
    expect(figures).toHaveLength(2);
    expect(words(figures[0] as Element)).toBe("Tool failedCould not render the wikipedia card.");
    // It is no longer the card that was asked for, and its neighbour is untouched.
    expect(figures[0]?.hasAttribute("data-card")).toBe(false);
    expect(figures[1]?.getAttribute("data-card")).toBe("calculator");
    expect(warn).toHaveBeenCalledWith("[cards] failed to render", "wikipedia", expect.any(Error));
    warn.mockRestore();
    error.mockRestore();
  });

  it("a kind this page does not know draws nothing", () => {
    const el = render(<ToolCard card={"hologram" as CardKind} data={{}} />).container;
    expect(el.innerHTML).toBe("");
    // Nor does a name borrowed from Object.prototype.
    expect(
      render(<ToolCard card={"constructor" as CardKind} data={{}} />).container.innerHTML,
    ).toBe("");
  });
});

describe("the entrance", () => {
  it("each card arrives a beat after the one before it, once", () => {
    const calls: Array<{ delay?: number }> = [];
    const animate = vi.fn(function (this: Element, _frames: unknown, options: { delay?: number }) {
      calls.push(options);
      return {} as Animation;
    });
    const original = Element.prototype.animate;
    Element.prototype.animate = animate as unknown as typeof Element.prototype.animate;
    try {
      const view = render(
        <>
          <ToolCard card="calculator" data={FULL.calculator} index={0} />
          <ToolCard card="convert" data={FULL.convert} index={2} />
        </>,
      );
      expect(calls.map((c) => c.delay)).toEqual([0, 80]);
      view.rerender(
        <>
          <ToolCard card="calculator" data={FULL.calculator} index={0} />
          <ToolCard card="convert" data={FULL.convert} index={2} />
        </>,
      );
      expect(calls).toHaveLength(2);
    } finally {
      Element.prototype.animate = original;
    }
  });
});
