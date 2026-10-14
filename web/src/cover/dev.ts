/* Preview tools: which foreground is showing, and how the frames are going.
 *
 * Imported by the development server only. A build contains none of it.
 *
 *   1, 2, 3     the three foregrounds (or the buttons, on a phone)
 *   h           hide or show this panel
 *   ?scene=2    start on a foreground
 *   ?hud=0      start with the panel hidden
 */

import type { Draw } from "./draw";
import { SCENES, type Sky } from "./sky";

export function mountDev(sky: Sky, draw: Draw): void {
  // Within reach of the console, and of the script that takes screenshots.
  (window as unknown as { sky: Sky }).sky = sky;

  const query = new URLSearchParams(location.search);
  const asked = Number(query.get("scene"));
  if (asked >= 1 && asked <= SCENES.length) sky.scene = asked - 1;

  const panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;top:max(10px,env(safe-area-inset-top));left:max(10px,env(safe-area-inset-left));" +
    "display:flex;gap:8px;align-items:center;padding:6px 8px;border-radius:4px;" +
    "background:rgb(0 0 0 / 0.55);color:rgb(235 242 238 / 0.9);" +
    "font:12px/1.3 system-ui,sans-serif;font-variant-numeric:tabular-nums";
  const show = (on: boolean): void => {
    panel.style.display = on ? "flex" : "none";
  };
  show(query.get("hud") !== "0");

  const buttons = SCENES.map((name, i) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${i + 1} ${name}`;
    button.style.cssText =
      "font:inherit;color:inherit;background:none;border:1px solid rgb(255 255 255 / 0.3);" +
      "border-radius:4px;padding:4px 8px;min-height:32px";
    button.addEventListener("click", () => {
      sky.scene = i;
      mark();
    });
    panel.append(button);
    return button;
  });
  const figures = document.createElement("span");
  panel.append(figures);
  document.body.append(panel);

  function mark(): void {
    buttons.forEach((button, i) => {
      button.style.borderColor =
        i === sky.scene ? "rgb(255 255 255 / 0.9)" : "rgb(255 255 255 / 0.3)";
    });
  }
  mark();

  window.addEventListener("keydown", (event) => {
    const n = Number(event.key);
    if (n >= 1 && n <= SCENES.length) {
      sky.scene = n - 1;
      mark();
    } else if (event.key === "h") show(panel.style.display === "none");
  });

  setInterval(() => {
    const { frame, scale, steps } = sky.stats;
    const rate = frame ? `${Math.round(1000 / frame)} fps · ${frame.toFixed(1)} ms` : "measuring";
    figures.textContent = `${rate} · buffer ${Math.round(scale * 100)}% · ${steps} sheets · chain ${draw.chain.length}`;
  }, 250);
}
