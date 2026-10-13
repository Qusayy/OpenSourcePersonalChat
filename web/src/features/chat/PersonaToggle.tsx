import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { bootstrap } from "@/lib/bootstrap";
import { reduced } from "@/lib/frame";
import { choosePersona, usePersona } from "./run";

/**
 * Who answers: plain words in a row, the chosen one inked and ruled.
 *
 * One rule slides under whichever persona is chosen. It is positioned from
 * the chosen button's own box, so it follows a wrap onto a second row and a
 * change of font; `moving` is added only after the first placement, so it
 * appears in place on load and travels thereafter. The row, the words and
 * the rule are styled in chat.css.
 */
export function PersonaToggle() {
  const persona = usePersona();
  const row = useRef<HTMLDivElement>(null);
  const ink = useRef<HTMLSpanElement>(null);

  const place = useCallback(() => {
    const host = row.current;
    const mark = ink.current;
    const chosen = host?.querySelector<HTMLElement>('.persona[aria-pressed="true"]');
    if (!host || !mark || !chosen?.offsetWidth) return;
    const pad = Number.parseFloat(getComputedStyle(chosen).paddingLeft) || 0;
    mark.style.setProperty("--x", `${chosen.offsetLeft + pad}px`);
    mark.style.setProperty("--y", `${chosen.offsetTop + chosen.offsetHeight - 3}px`);
    mark.style.setProperty("--w", String(Math.max(0, chosen.offsetWidth - pad * 2)));
    host.classList.add("inked");
    if (!reduced()) requestAnimationFrame(() => mark.classList.add("moving"));
  }, []);

  // After every render: the chosen persona may have changed.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `persona` is what moves the rule
  useLayoutEffect(place, [persona, place]);

  useEffect(() => {
    window.addEventListener("resize", place);
    document.fonts?.ready.then(place);
    return () => window.removeEventListener("resize", place);
  }, [place]);

  return (
    <div className="persona-row" id="persona-row" role="group" aria-label="Persona" ref={row}>
      {bootstrap().personas.map((p) => (
        <button
          key={p.id}
          type="button"
          className="persona"
          data-persona={p.id}
          aria-pressed={p.id === persona}
          title={p.blurb}
          onClick={() => choosePersona(p.id)}
        >
          {p.name}
        </button>
      ))}
      <span className="persona-ink" aria-hidden="true" ref={ink} />
    </div>
  );
}
