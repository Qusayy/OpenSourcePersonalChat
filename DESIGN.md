# Pass Plate — the design system

This records the world as it was actually built, not as it was planned. Every
value here is in the shipped CSS; every claim about behaviour is in the shipped
JS.

---

## 1. The thesis

**A conversation is a contact window with a small distant machine.**

The page is one satellite pass-prediction plate. An engraved graticule fills the
field, a ground track crosses it, and contact windows are marked along the
track. Asking a question opens a window; the first token is acquisition of
signal; the answer is the pass; the metrics are what the pass cost.

This replaced a centred column of message bubbles with a sidebar of chats —
the shape every product in this category ships. The old look is kept only as an
anti-reference.

### The two laws

Everything below follows from these, and breaking either one puts the page back
in the near-black-plus-neon cluster it was built to escape.

**1. Nothing glows.** This is printed ink on a matte plate, not a light source.
There are no `box-shadow` halos, no blurred accents, no bloom, no gradients
standing in for luminance. Where something must read as *live*, it gets more
ink or a moving edge — never more light. The two places this was tempting and
refused: the streaming caret (a flat square of signal ink) and the open contact
band (a travelling dashed edge, not a glow).

**2. The graticule is drawn at real density.** A faint decorative grid would
make this a dark theme with a texture. Cells are 44px on desktop and 30px below
700px, with every fourth line heavier, so the field is something you could
measure against.

A corollary the code enforces: **plates are cut, not rounded.** Square corners,
1px engraved edges, and depth carried by rules and fills rather than elevation.
There are no elevation shadows anywhere in this system — not as an oversight,
but because a printed plate has no z-axis.

---

## 2. Tokens

All in `app/static/css/tokens.css`.

### The plate

| Token | Value | Use |
|---|---|---|
| `--plate-0` | `#04060b` | page ground, code blocks, bar grooves |
| `--plate-1` | `#080b12` | standard plate: rail, HUD, panels, answers |
| `--plate-2` | `#0d1119` | raised plate: composer, palette, badges |
| `--plate-3` | `#131824` | pressed / hover, user messages, `kbd` |

### Engraved graticule

| Token | Value | Use |
|---|---|---|
| `--grat-minor` | `#131c28` | minor grid lines |
| `--grat-major` | `#1d2a3a` | every fourth grid line, inner rules |
| `--grat-edge` | `#2a3a4d` | plate borders, horizon, axis ticks |

### Ink

| Token | Value | Use |
|---|---|---|
| `--signal` | `#ff6b35` | the live pass, and only the live pass |
| `--signal-deep` | `#c84a1e` | a pass that has closed |
| `--signal-wash` | `rgba(255,107,53,.12)` | contact-window fill, chart areas |
| `--elev` | `#7fd4c1` | secondary series, links, success |
| `--caution` | `#f2b950` | queued, waiting |
| `--fault` | `#ff5d73` | failed pass |

Signal orange is the scarcest thing on the page. It marks the pass in progress,
the one primary action (**Open contact**), and the focused edge of the composer.
Spending it anywhere else would make everything look live.

### Readout ink

`--ink-0 #eef2f7` · `--ink-1 #b9c6d6` · `--ink-2 #8d9cb0` · `--ink-3 #6f7f93`

Derived against `--plate-1`. Measured, not assumed — see §7.

### Type

Two faces, no font binaries, no network fetch:

- `--font-ui` — a plain grotesk system stack, for prose only.
- `--font-mono` — a mono stack with true tabular figures, for **every
  measurement on the site** and for all engraved labels. A plate whose numbers
  change width as they count is a broken instrument, so `.num`/`.mono` set
  `font-variant-numeric: tabular-nums` and `font-feature-settings: "tnum" 1`.

Scale, deliberately dense — closer to a chart legend than a web page:

`--t-legend 10px` · `--t-xs 11.5` · `--t-sm 13` · `--t-md 14.5` ·
`--t-lg 16` · `--t-xl 21` · `--t-2xl 30` · `--t-readout 44`

`.legend` is the workhorse: mono, 10px, `0.14em` tracking, uppercase,
`--ink-3`. It is the engraved label used for every unit, axis and section
heading in the system.

There is exactly one `--t-readout` numeral on screen at a time: live
throughput, in the HUD.

### Geometry and motion

Radii are `0` / `2px` / `4px` — effectively square. Durations are
`120 / 240 / 420ms`. Two easings, and the distinction is load-bearing:

- `--ease-draw` `cubic-bezier(.16,1,.3,1)` — things being *drawn* or arriving.
- `--ease-mech` `cubic-bezier(.65,0,.35,1)` — mechanical, symmetric state
  changes: lamps, glyph swaps, spinners.

Layout: `--rail-w 232px`, `--hud-w 268px`, `--col-max 760px`,
`--header-h 52px`.

---

## 3. The plate layer

`app/static/js/plate.js` draws two full-field SVGs behind the page:
`#graticule` (static, redrawn on resize) and `#track` (the pass).

**The track is not decoration.** Its axes carry real data:

- **x** — seconds elapsed since generation began
- **y** — instantaneous tokens per second

So the background of the page *is* the throughput plot, at plate scale. This is
what keeps the direction honest: a field of meaningless curves would read as a
world map and imply location data this product does not have. Plotting the one
quantity the product actually measures makes it an instrument instead.

Two decisions that follow from that:

- The x axis starts at the `start` event, not at submit, so **queue wait is
  never folded into the model's measured speed**. Queue position has its own
  notice.
- The axis auto-ranges (`max(6s, …)` with coarsening steps) and the y ceiling
  is a nice round number above the observed peak. A fixed axis crushed a
  two-second pass into a sliver at the origin.

### Phases

| Phase | What is on the plate |
|---|---|
| `idle` | graticule, horizon, a dashed **predicted** arc, apex node. **No axis numbers** — nothing has been measured yet. |
| `acquiring` | prefill. A contact band opens at the current time; its edge travels (marching ants). Label: `ACQUIRING…` |
| `live` | first token is **AOS**: the mark drops with the real elapsed ms, axis numbers appear, the track extends. |
| `closed` | **LOS** mark drops, the band closes onto exactly `[AOS, LOS]`, the track settles from `--signal` to `--signal-deep`. |
| `failed` | band and closing mark turn `--fault`; label reads `PASS FAILED`. |

The idle plate labels only the apex. AOS and LOS sit low on the arc, exactly
where the content column is, and their labels collided with the page's own text.

### The field is the visible plate

`.plate-field` is inset to the centre column (`inset: 0 var(--hud-w) 0
var(--rail-w)`), tracking the shell's own breakpoints. Full-bleed was the first
instinct and it was wrong: the rail is 232px of opaque plate, and a short pass
lives entirely inside that first 232px, so the entire signature interaction was
drawn behind the sidebar.

---

## 4. Components

**Station identity** (`.station`) — top of the rail. An authored SVG mark: a
graticule cell with the track crossing it and the AOS node on the apex. The
same mark is the favicon, drawn inline as a data URI.

**Craft header** (`.craft`) — the model stated the way a spacecraft under
observation would be: status lamp, designation, then params and quant as a
legend. The lamp (`.dot`) is a filled square in a ring; it changes colour and
ring offset, never brightness.

**Pass log** (`.convo-list`) — conversation history. Rows carry a left rule that
turns `--signal` for the open one.

**The brief** (`.brief`) — the first viewport, left-aligned because an
instrument annotates from the edge of the plate. Kicker legend, the model
designation set large in mono, one line of prose, then the **spec slab**: a
ruled tabular grid of params / quant / context / threads / hardware. There is
no hero paragraph; the plate behind it is already showing a predicted pass.

**Thread** — the centre column is transparent so the plate reads through it,
but every block carrying text sits on an opaque cut plate. No prose is ever
rendered over the graticule or the track.

- *Uplink* (`.msg.user`) — a pressed plate, right-aligned, with a neutral right
  rule. No accent: the accent belongs to the pass.
- *Downlink* (`.msg.bot`) — a full-width plate with a left rule. The rule is
  `--signal` while the answer is streaming and `--signal-deep` once it settles,
  driven by `:has(.caret)` rather than by a JS state flag.
- *Caret* — a flat square of signal ink, appended **inside** the last rendered
  block so it sits after the newest word rather than below the paragraph.
- *Foot* (`.msg-foot`) — the pass's own telemetry line in engraved legend caps.

**Timeline** (`.timeline`) — the steps that produced the answer, read as a
station log against a left rule rather than a row of pills. Status is carried
by the glyph cell's border colour: signal (running, pulsing opacity), elev (ok),
fault (failed).

**Tool cards** (`.card-tool`) — cut slabs with a ruled head. Weather art,
clock faces and monograms are all recoloured into plate inks so a forecast does
not arrive as the only full-colour object on the page.

**Contact band** (`.composer`) — the open window. Square, heavy top rule, and
that rule turns `--signal` on focus. Personas are legend tabs divided by rules,
the armed one carrying an inset signal underline.

**Telemetry slab** (`.hud`) — the one large readout, a flat-fill sparkline, the
context arc, and the station log as dotted-rule rows. Collapses to `.hud-strip`,
a single scrolling line, below 1200px.

---

## 5. Motion grammar

Things are **drawn** and they **travel**. Nothing fades in place, nothing
glows, nothing bounces.

| Where | What |
|---|---|
| contact band, acquiring | dashed edge travels (`@keyframes ants`, 900ms linear) |
| track | extends with the data; head carried forward to the present instant |
| status lamp | ring offset breathes 2px→4px (`@keyframes lamp`) |
| messages, toasts | `rise` — a short translate, `--ease-draw` |
| send glyph | go/stop crossfade with a lateral slide, `--ease-mech` |
| context arc | `stroke-dashoffset` transition, `--ease-draw` |
| bars, progress | `scaleX` from the left, never animated `width` |

---

## 6. What this world refuses

- Rounded cards, pills and capsules.
- Elevation shadows and coloured halos.
- Gradients used as luminance (the one surviving gradient is the composer
  scrim, which is a fade to the page ground, not a glow).
- A hero headline-plus-paragraph on the first viewport.
- Proportional figures in any measurement.
- Decoration that plots nothing.

---

## 7. Accessibility, as measured

These were verified on the rendered page, not inferred from tokens.

- **Contrast.** Every visible text run was sampled on the live pages —
  computed colour composited over its real rendered ground, WCAG ratio against
  the AA threshold for its size and weight. Chat (empty and after a pass),
  bench, about, and chat at 390px: **0 pairs below AA**. The one failure the
  probe caught was the `·` separator in the message foot at 1.7:1; it now
  inherits the foot's own colour.
- **Reduced motion.** `prefers-reduced-motion: reduce` stops the plate's clock
  loop entirely — the band never creeps, the track never sweeps — but
  `sample()` still repaints on data. A closed pass renders complete: band, both
  marks, the track and both axes. Removing measurements would be removing
  information, not motion. Ambient loops and travel are killed with `!important`
  because later stylesheets cannot be reached by source order.
- **Touch targets.** 44px minimum under `(pointer: coarse)` for icon buttons,
  delete buttons, personas, palette rows, nav links and the send button;
  hover-only affordances (message actions, delete) are made permanent under
  `(hover: none)`.
- **Safe areas.** `env(safe-area-inset-*)` on the rail, topbar, HUD, hud-strip
  and composer.
- **Announcements.** The answer is not a live region — its markup is rebuilt ten
  times a second while streaming. Milestones go to a single `#sr-status`
  channel instead.
- **The `hidden` trap.** `[hidden]` loses to any `display` declaration, so
  `.timeline`, `.msg-foot`, `.skill-active` and `.palette` each carry an
  explicit `[hidden] { display: none }`.
- **Console.** Chat (normal and reduced motion), bench and about: zero errors,
  exceptions or failed requests.

---

## 8. File map

| File | Owns |
|---|---|
| `css/tokens.css` | tokens, resets, `.legend`, `.num`, scrollbars, `.sr-only` |
| `css/plate.css` | the shell: plate field, rail, topbar, buttons, HUD, toasts, breakpoints, reduced motion |
| `css/chat.css` | brief, thread, prose, caret, notices, contact band |
| `css/cards.css` | timeline, tool cards, skill tiles, command palette |
| `css/bench.css` | bench + about: panels, charts, tables |
| `js/plate.js` | graticule and ground-track renderer, the phase machine |
| `js/app.js` | shell: plate construction, rail drawer, toasts, health polling |
| `js/chat.js` | chat controller; drives the plate's phases from the SSE stream |

`.legend` and `.track` belong to the plate world. The bench chart key is
`.chart-key` and the bar groove is `.bar-track`; the context arc's backing
circle is `.ring-bg`. Two unrelated things answering to one selector is how a
design system starts lying about itself.
