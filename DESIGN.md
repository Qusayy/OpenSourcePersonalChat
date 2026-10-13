---
name: Aurora
description: A plain sheet you write on, with the machine's cost on one status line along its bottom edge.
colors:
  sheet: "oklch(98.5% 0.004 150)"
  sheet-2: "oklch(95.5% 0.007 150)"
  rule: "oklch(87% 0.01 150)"
  ink: "oklch(22% 0.015 150)"
  ink-2: "oklch(45% 0.018 150)"
  live: "oklch(46% 0.12 152)"
  fault: "oklch(48% 0.19 27)"
  caution: "oklch(47% 0.095 70)"
  scrim: "oklch(22% 0.015 150 / 0.32)"
  bar-step: "oklch(64% 0.012 150)"
  c1: "#2a78d6"
  c2: "#e87ba4"
  c3: "#6250d6"
  c4: "#eb6834"
  sheet-dark: "oklch(17% 0.01 150)"
  sheet-2-dark: "oklch(21.5% 0.012 150)"
  rule-dark: "oklch(31% 0.014 150)"
  ink-dark: "oklch(93% 0.01 150)"
  ink-2-dark: "oklch(72% 0.018 150)"
  live-dark: "oklch(80% 0.19 150)"
  fault-dark: "oklch(74% 0.14 27)"
  caution-dark: "oklch(82% 0.13 82)"
  scrim-dark: "oklch(8% 0.01 150 / 0.6)"
  bar-step-dark: "oklch(58% 0.016 150)"
  c1-dark: "#3987e5"
  c2-dark: "#d55181"
  c3-dark: "#9085e9"
  c4-dark: "#d95926"
typography:
  display:
    fontFamily: "Recursive, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans', sans-serif"
    fontSize: "3.25rem"
    fontWeight: 700
    lineHeight: 1.04
    letterSpacing: "-0.03em"
    fontVariation: "'MONO' 0, 'CASL' 0"
  headline:
    fontFamily: "Recursive, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans', sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Recursive, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans', sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Recursive, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans', sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
    fontVariation: "'MONO' 0, 'CASL' 0"
  label:
    fontFamily: "Recursive, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans', sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: 1.55
    letterSpacing: "normal"
  caption:
    fontFamily: "Recursive, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans', sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  figure:
    fontFamily: "Recursive, ui-monospace, 'Cascadia Mono', 'Segoe UI Mono', Consolas, 'Liberation Mono', monospace"
    fontSize: "2rem"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontVariation: "'MONO' 1, 'CASL' 0"
    fontFeature: "'tnum'"
  numeral:
    fontFamily: "Recursive, ui-monospace, 'Cascadia Mono', 'Segoe UI Mono', Consolas, 'Liberation Mono', monospace"
    fontVariation: "'MONO' 1, 'CASL' 0"
    fontFeature: "'tnum'"
rounded:
  r-1: "4px"
  r-2: "8px"
spacing:
  s-1: "4px"
  s-2: "8px"
  s-3: "12px"
  s-4: "16px"
  s-5: "24px"
  s-6: "32px"
  s-7: "48px"
  s-8: "64px"
  s-9: "96px"
  edge: "24px"
  col: "42rem"
  bar-h: "56px"
components:
  button:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.r-1}"
    padding: "0 16px"
    height: "36px"
  button-hover:
    backgroundColor: "{colors.sheet-2}"
  button-disabled:
    textColor: "{colors.ink-2}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sheet}"
    typography: "{typography.label}"
    rounded: "{rounded.r-1}"
    padding: "0 16px"
    height: "36px"
  button-primary-disabled:
    backgroundColor: "{colors.rule}"
  button-stop:
    backgroundColor: "transparent"
    textColor: "{colors.fault}"
    rounded: "{rounded.r-1}"
  text-button:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.r-1}"
    padding: "4px 8px"
  text-button-hover:
    backgroundColor: "{colors.sheet-2}"
  text-button-quiet:
    textColor: "{colors.ink-2}"
  icon-button:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.r-1}"
    size: "32px"
  icon-button-hover:
    backgroundColor: "{colors.sheet-2}"
    textColor: "{colors.ink}"
  input:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "8px 0"
  status-line:
    backgroundColor: "{colors.sheet-2}"
    textColor: "{colors.ink-2}"
    typography: "{typography.caption}"
    padding: "4px 24px"
    height: "2.5rem"
  palette:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.r-2}"
    padding: "4px"
  palette-row-selected:
    backgroundColor: "{colors.sheet-2}"
    rounded: "{rounded.r-1}"
    height: "36px"
  toast:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sheet}"
    rounded: "{rounded.r-1}"
    padding: "8px 16px"
  toast-error:
    backgroundColor: "{colors.fault}"
  code-block:
    backgroundColor: "{colors.sheet-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.r-1}"
    padding: "12px 16px"
---

# Design System: Aurora

## Overview

**Creative North Star: "The Sheet and the Status Line"**

Aurora is a plain sheet you write on. The page is three rows and nothing beside them: a top bar, the sheet, and one status line pinned to the bottom edge. There is no sidebar of chats, no centred column of bubbles and no dashboard column of metric cards. History opens on demand as a dialog, and every fact about the machine lives once, in the status line, which comes alive while the model writes.

The material is paper and ink. A near-white sheet tinted toward green-grey, two inks, hairline rules and 4px corners. Structure is drawn with rules and space, never with boxes around text. Colour is almost absent, so that the one aurora green can mean a single thing: this is happening right now. Type is one family, Recursive, proportional for words and switched to its fixed-width cut for numerals, so a figure that ticks sits in a sentence without changing voice or shifting its neighbours.

The system refuses gradients, glass and backdrop blur, glow, pills, message bubbles, avatar tiles, a sparkle mark, uppercase tracked micro-labels, themed vocabulary with renamed labels, a black-and-orange palette, and monospace set on words. The themed vocabulary, the cold mono-caps look and the black-and-orange palette are the user's stated reasons for reverting an earlier redesign; the rest were in the plan the user approved, except the monospace rule, which came out of review.

**Key Characteristics:**
- Three rows: top bar (56px), sheet, status line. Nothing sits beside them.
- One left edge shared by the wordmark, the reading column and the composer; the status line alone runs the full width.
- Two inks and no third. Hierarchy comes from size, weight and space.
- One live colour, reserved for what is live.
- One typeface. Numerals and units fixed-width, words proportional.
- Hairline rules instead of boxes; flat surfaces; one shadow, only for things that float.
- Every fact said once, in the place it belongs.
- Light is the designed case; dark follows the visitor's OS. No theme switch in the interface.

## Colors

A green-grey sheet and two inks, every neutral tinted toward the accent hue (150°) so the greys stay of a piece with the green. Authored in OKLCH; the values in the frontmatter are normative.

### Primary
- **Aurora Live** (`live`, `live-dark`): marks what is happening right now. The status line's state dot and state word during a request phase, the running tok/s figure and its trace, the writing caret in the thread and in the input, a running step in the route line, and the benchmark's progress bar. It is not a brand colour and not a selection colour.

### Secondary
- **Fault Red** (`fault`, `fault-dark`): a failed tool's caption, the "model unavailable" state, the Stop button's outline and word, the error toast, a destructive hover, the character count past its limit.
- **Caution Amber** (`caution`, `caution-dark`): waiting states (starting, loading, queued), the queue notice, context use above 85%, the mark on a data caveat.

### Tertiary
- **Budget Blue, Magenta, Violet, Orange** (`c1`..`c4`, with dark steps): the four categories of the memory budget, in that fixed order, and nowhere else. None is a hue the interface uses for state. The light-scheme magenta sits under 3:1 against the sheet, so every segment is also named with its value in the legend beneath.

### Neutral
- **Sheet** (`sheet`): the page.
- **Sheet Tint** (`sheet-2`): the status line's ground, hover fills, code blocks, chart tracks. The only second surface.
- **Rule** (`rule`): hairlines, dividers, the context and progress tracks, scrollbars.
- **Ink** (`ink`): text, the 2px rule under the input, the primary button, focus outlines, chart marks (aliased as `--bar`).
- **Second Ink** (`ink-2`): supporting text, captions, placeholders, inactive navigation. 7.1:1 on the sheet in light, 7.8:1 in dark.
- **Bar Step** (`bar-step`): the same ink one step fainter, for the second of an ordered pair in a chart (p95 beside p50). Fainter, not lighter: on the dark sheet it is the darker of the two.
- **Scrim** (`scrim`): the backdrop behind the history dialog.

### Named Rules

**The Live Rule.** `live` marks the thing that is happening now and nothing that is merely selected, chosen, successful or important. Where you are and what you chose are said in ink with a 2px underline. If nothing on the page is in progress, nothing on the page is green.

**The Two Inks Rule.** There are two inks and no third. A fainter grey is not available to reach for; hierarchy comes from size, weight and space.

**The Ink Chart Rule.** A single series is drawn in ink, because there is no category for a colour to name and a hue there would read as the page's second accent. An ordered pair is ink and `bar-step`. Only the memory budget, the one chart with categories, takes `c1`..`c4`.

**The Two Schemes Rule.** Dark follows `prefers-color-scheme`. `data-theme="light"` or `data-theme="dark"` on `<html>` pins one. There is no switch in the interface. Running text drops from weight 400 to 360 on the dark sheet, where light text reads heavier.

## Typography

**Display Font:** Recursive variable (with ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Noto Sans, sans-serif)
**Body Font:** Recursive, the same family
**Label/Mono Font:** Recursive with the MONO axis at 1 (with ui-monospace, Cascadia Mono, Segoe UI Mono, Consolas, Liberation Mono, monospace)

**Character:** One self-hosted variable face (SIL OFL 1.1) doing every job. The MONO axis turns the same letterforms fixed-width, so prose and figures share a voice; CASL stays at 0 throughout. Emphasis is the face's own 15° oblique; bold is 600.

### Hierarchy
- **Display** (700, 3.25rem / 52px, line-height 1.04, -0.03em, balanced): the page headline. One per page. 2.25rem at phone width.
- **Headline** (600, 1.5rem / 24px, line-height 1.2, -0.015em): section headings on the long-form pages.
- **Title** (600, 1.25rem / 20px, line-height 1.35, -0.01em): the visitor's question heading its exchange; the dialog heading; a fetched page's title. Also the size of the input on the empty sheet.
- **Body** (400, 1.0625rem / 17px, line-height 1.55): answers, paragraphs, the input. Long-form text stops at 40rem; the chat column at 42rem.
- **Label** (600 on buttons, 500 on word-buttons, 400 on navigation and rows; 0.9375rem / 15px): controls, navigation, list rows, tables, tool figures.
- **Caption** (400, 0.8125rem / 13px, line-height 1.5): the status line, figure captions, the cost line under a reply, the route line, chart axes. Sentence case, no tracking.
- **Figure** (600, 2rem / 32px, line-height 1.1, -0.02em, fixed-width): a number that is the answer, the same size wherever it appears (calculator result, temperature, clock, converted amount).

### Named Rules

**The Thirteen Floor Rule.** Nothing is set below 13px (`--t-1`). The input is never below 16px, which is what keeps iOS from zooming a focused field.

**The Fixed Figures Rule.** Only numerals and their units take the fixed-width cut, with tabular numerals, so a number that changes every frame does not reflow the line around it. The words beside a figure are words and are set proportional: "8.4" is fixed, "tok/s" is not. Code, `kbd` and preformatted text are fixed-width because they are code; that is the only other use.

**The One Face Rule.** No second typeface, and no system face standing in for display. Weight and size carry the hierarchy.

## Layout

The body is a three-row grid: a 56px top bar, the sheet taking the rest, the status line at its natural height. The page itself does not scroll; the sheet does.

One left edge, `--pad`, is shared by the wordmark, the reading column and the composer. It sits a third of the spare width in from the left: `max(24px, (100vw - 42rem) / 3)`. The column is therefore neither jammed against the window edge nor centred like a card. The right edge of everything is `--edge` (24px). The chat column measures 42rem; the long-form pages allow charts 56rem and hold their text to 40rem.

Spacing is a nine-step scale: 4, 8, 12, 16, 24, 32, 48, 64, 96px. Sections on the long-form pages are parted by 64px; a question takes 48px above and 12px below, like any heading.

The chat page has two views. Empty: headline, one paragraph, the composer directly under them, then the skills, all scrolling as one sheet. Thread: the composer sits above the status line under a hairline and the thread scrolls above it. DOM order is visual order in both, so tab order never disagrees with the screen.

Responsive behaviour is by need rather than by device. Below 1440px the rate trace narrows. Below 1340px the chat page's status line gives its figures a row of their own from the start, rather than wrapping at the moment an answer begins. Below 760px keyboard hints go. Below 680px a bar chart row stacks its label and value above a full-width bar. Below 640px a table becomes one block per row with each cell carrying its own heading. Below 620px the headline drops to 2.25rem, skill rows stack, the page links move into the history dialog on the chat page, and the status line becomes a corridor (see Components). Touch is detected by `pointer: coarse`, not width: targets take explicit 44px minimums.

**The One Left Edge Rule.** The wordmark, the column and the composer hang from `--pad`. The status line is the single exception: it runs the full width of the window, `--edge` in from each side, on every page, because what the machine is and what a request is costing do not fit one row from the column's edge, and the same line on every page should start in the same place.

## Elevation & Depth

Flat. Surfaces are the sheet and one tint of it; separation is a hairline. There is exactly one shadow (`--sh-pop`), and it belongs to things that float over the sheet.

### Shadow Vocabulary
- **Pop** (`box-shadow: 0 1px 2px oklch(22% 0.015 150 / 0.08), 0 12px 32px -8px oklch(22% 0.015 150 / 0.22)`; in dark, `0 1px 2px oklch(0% 0 0 / 0.4), 0 12px 32px -8px oklch(0% 0 0 / 0.6)`): the command palette, the history dialog, toasts.

Layers are four: status line (10), popovers (30), toasts (60), the skip link (200).

### Named Rules

**The Floats Only Rule.** A shadow means the element is over the sheet and will go away. Nothing that sits in the sheet has one. No glow, no blur, no translucent surface.

## Shapes

Hairlines and small corners. The default radius is 4px (`r-1`): buttons, hover fills, code blocks, the focus outline, the data end of a bar. 8px (`r-2`) is used once, on the floating command palette. Circles are reserved for dots: the state dot, the newest sample on a trace, a measured value's ring. Nothing is a pill.

Rules are 1px in `rule`. Two get more: the 2px ink rule under the input, which is its only boundary, and the 2px ink underline that marks the current page or the chosen persona. A table head and a quotation take a 1px rule in the second ink.

Focus is a 2px ink outline, offset 2px. The input, which has no box to ring, thickens its rule by 1px instead.

**The Rule, Not Box Rule.** A list is ruled rows, a panel is a block under a top rule, a tool result is a figure between two hairlines, a warning is a ruled note. Running text, questions, answers and results are never enclosed. Enclosure is kept for things that are literally controls or code: an outlined button, a `kbd`, the `mock` tag, a code block's tint, an error notice's tint.

## Components

Controls are words first. A box appears only where position alone would not say "this is a control".

### Buttons
- **Shape:** gently squared (4px), 36px tall, 16px side padding, 15px at weight 600.
- **Outline (default):** 1px ink border on a transparent ground; hover fills with the sheet tint; pressed moves down 1px; disabled drops to a rule-coloured border and the second ink.
- **Primary:** solid ink with sheet-coloured text; hover mixes the ink 84% with the sheet. One per view: Send, Run the benchmark.
- **Stop:** while a reply streams, Send becomes Stop: fault-coloured outline and word on a transparent ground.
- **Word button:** no border, 4px 8px padding, weight 500, hover tint. Used where position already says it is a control: the top bar, a caption, the actions under a reply. A quiet variant starts in the second ink. Actions are always present and always words ("Copy", "Ask again"), never hover-only and never an unlabelled icon.
- **Icon button:** 32px square (44px on touch), second ink, hover tint. Only for the per-row delete in history, with an accessible name.
- **Transitions:** colour and background over 120ms.

### Inputs / Fields
- **Style:** a borderless textarea on a 2px ink rule, with Send at the right end of the same rule. No box, no fill. 17px in a thread, 20px on the empty sheet, never under 16px. Grows to 208px, then scrolls.
- **Focus:** the rule thickens by 1px; the caret is the live colour.
- **Beneath it:** personas as plain words, the chosen one inked with a 2px underline at the same weight so choosing never shifts its neighbours; a `/` keyboard hint; a character count that turns fault-coloured at the limit.

### Navigation
- **Top bar:** the wordmark (17px, 700) on the shared left edge, three page links in the second ink at 15px, then the page's word buttons at the right. The current page is inked with a 2px underline offset 7px. No filled tab.
- **History:** a real `<dialog>` that slides in from the right (min(88vw, 24rem)), with a scrim behind it. Conversations are ruled rows: a title, a count, and a sibling delete button. The current one is weight 600.
- **Contents list:** on long-form pages, a wrapped row of second-ink links under the page head.

### Lists and rows
- **Skill rows:** six ruled rows, not six cards: a name at 600, what it does in the second ink, and the tools it runs through at 13px. Hover tints the row and extends 12px past the text on both sides so the row reads as one target.
- **Fact lists:** term in the second ink at the left, value at the right, a hairline under each. The headline figures of a benchmark run are the same list with the numeral given weight.
- **Command palette:** the one floating panel (8px corners, hairline border, pop shadow). Rows are a fixed 36px (48px on touch), the list snaps to whole rows, and the highlighted command's description is set in full beneath the list.

### Cards / Containers
There are none. A **panel** is a block with 16px above it and a hairline on top. A **caveat** is a ruled note: a caution-coloured mark, then words in ink.

### Thread
- **Question:** set as a heading (20px, 600). No bubble; weight and size say whose line it is. A long paste is set as body text and folded to five lines.
- **Answer:** body text at the column measure, straight on the sheet. While it streams, a 2px live-coloured caret marks the writing position.
- **Cost line:** under each finished reply, 13px in the second ink: tok/s, time to first token, tokens, time to answer, each numeral fixed-width in ink; then Copy and Ask again.
- **Route line:** one line of 13px text above a reply, steps parted by hairlines. A step is drawn while it runs, in the live colour with the word "running", and afterwards only if it was a routing decision worth knowing.
- **Notices:** a line in the thread with a mark. Queue notices take caution; an error is the one tinted block (8% fault in the sheet), with a Try again button.

### Tool figures
A tool result is a `<figure>` between two hairlines: a 13px caption, then what the tool returned. No box, no fill, no border colour. The caption carries a small drawn icon, the tool's name in ink at 600, a qualifier in the second ink ("exact", a place, a domain), and how long it took at the right end in fixed-width numerals. A number that is the answer is set at 32px. A failed tool is shown failing: icon and name in the fault colour, the message as ordinary text.

**The Caption Rule.** A finished tool is named, qualified and timed only by its figure's caption. The route line drops the step once the figure exists, and the cost line does not count it again.

### Charts
- **Bars:** a 20px track in the sheet tint, the bar in ink, square at the baseline with a 4px data end; label at the left, fixed-width value at the right.
- **Ordered pair:** p50 in ink, p95 in `bar-step`.
- **Range strip:** a hairline axis with 8px bands; reference machines recede (second ink mixed 45% into the sheet), this server is full ink, and the measured value is an open ring.
- **Line:** a 2px ink series over a 7% ink area, hairline grid, 13px labels.
- **Memory budget:** a 28px stacked bar with 2px gaps in `c1`..`c4`, and a legend that names every segment with its value.
- **Tables:** right-aligned fixed-width figures, a proportional first column, hairlines under rows and a second-ink rule under the head.

### The status line (signature)
One row along the bottom edge of every page, like an editor's: sheet-tint ground, a hairline above, 13px in the second ink, at least 2.5rem tall, 24px in from each side of the window.

- **Left, and it does not move:** the state (an 8px dot and a word at 600), the model and its quantisation, the hardware and thread count. A bordered `mock` tag appears when answers are canned and is never hidden.
- **Right, anchored to the right edge:** at rest, context use on the chat page (count, limit, a 56px by 4px bar) and resident memory when the server reports it. Each figure sits in a slot as wide as its largest value, so a number that ticks changes in place.
- **States:** ready in ink; starting, loading and queued in caution; model unavailable in fault; working, reading and writing in the live colour, as is a result held for a tab that was not being watched. The word slot is fixed-width, so states swap in place. While waiting, the dot breathes (1.4s) and a seconds count appears after two seconds. Once tokens arrive the dot holds still and the figures do the moving.
- **In flight:** on the first token, tok/s with a live-coloured trace (newest sample dotted), tokens so far, and time to first token join the right side. The context bar turns caution above 85%.
- **Narrow:** below 1340px the chat page's figures take their own row. Below 620px the line is a corridor: state plus the one figure that matters now (the rate while writing, context otherwise), with the rest behind a "Details" word button.

**The Say It Once Rule.** What the machine is and what state it is in are here and only here. What a request is costing is here only while the request is in flight; when it ends, the figures leave the line and are printed under the reply they belong to. A figure with nothing to report is absent, not a dash and not a zero.

### Motion
State changes only: 120ms for colour, 200ms for position, on `cubic-bezier(0.16, 1, 0.3, 1)`. There is one entrance, for things that arrive in the thread (6 to 8px rise, 200ms), and one authored move: the composer travels from under the headline to the foot of the sheet when the first question is sent. The status line is the one place the page moves on its own. Bars and meters animate `scaleX`, never width. Under reduced motion the loops stop and travel is removed, while colour and opacity transitions stay so the interface still acknowledges input.

## Do's and Don'ts

### Do:
- **Do** hang new content from the shared left edge (`--pad`) and stop it at the column measure (42rem for chat, 40rem for long-form text).
- **Do** run the status line the full width of the window, 24px in from each side, on every page.
- **Do** keep `live` for what is in progress: the state dot and word, the running rate and its trace, the caret, a running step, a progress bar.
- **Do** mark selection and location in ink with a 2px underline.
- **Do** set numerals and units fixed-width with tabular figures, and the words beside them proportional.
- **Do** separate with a 1px rule and space; set a result as a captioned figure between two hairlines.
- **Do** draw a single-series chart in ink and an ordered pair in ink and `bar-step`.
- **Do** say each fact once: machine and state in the status line, a reply's cost under the reply, a tool's name and time in its caption.
- **Do** leave a figure out when it has nothing to report.
- **Do** label actions with words and keep them visible without hover.
- **Do** give touch targets a 44px minimum under `pointer: coarse`.
- **Do** check every new colour pair against both sheets; the floor is 4.5:1.

### Don't:
- **Don't** use gradients.
- **Don't** use glass, backdrop blur or translucent surfaces.
- **Don't** use glow.
- **Don't** make pills. Corners are 4px; the one floating panel is 8px.
- **Don't** put messages in bubbles or add avatar tiles.
- **Don't** add a sparkle mark.
- **Don't** set uppercase tracked micro-labels. Captions are sentence case at 13px.
- **Don't** theme the vocabulary or rename labels. Send is Send, History is History.
- **Don't** use a black-and-orange palette.
- **Don't** set words in monospace. The fixed-width cut is for numerals, units and code.
- **Don't** add a third ink, or set anything below 13px.
- **Don't** use `live` as a brand, selection, success or emphasis colour.
- **Don't** use `c1`..`c4` outside the memory budget, or give a single series a hue.
- **Don't** box running text, or add a sidebar of chats, a centred bubble column or a column of metric cards.
- **Don't** add a theme switch; the scheme follows the OS.
- **Don't** repeat the cost figures in the status line once a reply has finished.
- **Don't** put a shadow on anything that sits in the sheet.
