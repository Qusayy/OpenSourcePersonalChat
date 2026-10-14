/* How the frames are going, for tuning the sky.
 *
 * Imported by the development server only. A build contains none of it.
 * Shown with ?hud=1; `h` hides or shows it.
 */

import type { Draw } from "./draw";
import type { Sky } from "./sky";

export function mountDev(sky: Sky, draw: Draw): void {
  // Within reach of the console, and of a script that takes screenshots.
  (window as unknown as { sky: Sky }).sky = sky;

  const panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;top:max(10px,env(safe-area-inset-top));left:max(10px,env(safe-area-inset-left));" +
    "padding:6px 8px;border-radius:4px;background:rgb(0 0 0 / 0.55);color:rgb(235 242 238 / 0.9);" +
    "font:12px/1.3 system-ui,sans-serif;font-variant-numeric:tabular-nums";
  const show = (on: boolean): void => {
    panel.style.display = on ? "block" : "none";
  };
  show(new URLSearchParams(location.search).get("hud") === "1");
  document.body.append(panel);

  window.addEventListener("keydown", (event) => {
    if (event.key === "h") show(panel.style.display === "none");
  });

  setInterval(() => {
    const { frame, scale, steps } = sky.stats;
    const rate = frame ? `${Math.round(1000 / frame)} fps · ${frame.toFixed(1)} ms` : "measuring";
    panel.textContent = `${rate} · buffer ${Math.round(scale * 100)}% · ${steps} sheets · chain ${draw.chain.length}`;
  }, 250);
}
