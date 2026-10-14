/* The cover: the page a visitor is given.
 *
 * A night sky with the northern lights over it, a line of text saying what
 * night it is, and stars that can be joined with a line. It shares nothing
 * with the application behind it: no framework, none of its styles, and only
 * the frame loop from its library, so this is all a visitor's browser fetches.
 */

import "./cover.css";
import { mountDraw } from "./draw";
import { mountAnySky } from "./flat";
import { mountReadout } from "./readout";
import { open, tryChain } from "./unlock";

const canvas = document.querySelector<HTMLCanvasElement>("#sky");
const readout = document.querySelector<HTMLElement>("#readout");

if (readout) mountReadout(readout);

const mounted = canvas ? mountAnySky(canvas) : null;
if (mounted) {
  const { sky } = mounted;
  const draw = mountDraw(mounted.canvas, sky, {
    // A chain long enough to be the pattern is put to the server. A no
    // changes nothing here: the lines fade as any drawing's do.
    onChain: (chain) => {
      void tryChain(chain).then((right) => {
        if (right) open();
      });
    },
  });
  if (import.meta.env.DEV) void import("./dev").then((dev) => dev.mountDev(sky, draw));
} else {
  // Nothing to draw on at all: the stylesheet paints a plain night.
  document.documentElement.dataset.flat = "";
}
