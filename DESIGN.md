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
  page: "76rem"
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

There is one exception to the first and third of those, and it has a name: the aurora, a light that rises from the status line while an answer is being produced. The user asked for one live generation effect. It is the only luminous thing on any page, and it is not there when nothing is in progress.

**Key Characteristics:**
- Three rows: top bar (56px), sheet, status line. Nothing sits beside them.
- Everything is centred: the top bar, the reading column and the status line share one axis, with equal margins either side at every width.
- Two inks and no third. Hierarchy comes from size, weight and space.
- One live colour, reserved for what is live.
- One light, the aurora, and only while an answer is in flight.
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

**The Live Rule.** `live` marks the thing that is happening now and nothing that is merely selected, chosen, successful or important. Where you are and what you chose are said in ink with a 2px underline. If nothing on the page is in progress, nothing on the page is green. The aurora is the live colour at its largest and is held to the same rule.

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

Everything is centred. The top bar's content and the status line's sit in one container `--page` wide (76rem), inset `max(24px, (100vw - 76rem) / 2)` each side, so both start and end on the same two lines at any window width. The reading column, `--col` (42rem), is centred in the window and is the same on all three pages, so the left edge of the text does not move between them. On the long-form pages text, fact lists and bar charts run the column's full width; the two things that need more (the eight-column table and the history chart) break out of it by the same amount on each side, to at most 56rem, and stay on the axis. `--edge` (24px) is the least space anything keeps from the window. Columns inside a scroller are centred with auto margins, not viewport arithmetic, and each scroller reserves a scrollbar gutter on both sides so a scrollbar cannot pull the column off the axis.

Spacing is a nine-step scale: 4, 8, 12, 16, 24, 32, 48, 64, 96px. Sections on the long-form pages are parted by 64px; a question takes 48px above and 12px below, like any heading.

The chat page has two views. Empty: headline, one paragraph, the composer directly under them, then the skills, all scrolling as one sheet. Thread: the composer sits above the status line under a hairline and the thread scrolls above it. DOM order is visual order in both, so tab order never disagrees with the screen.

Responsive behaviour is by need rather than by device. Below 1264px, where the window is narrower than the container, the chat page's status line gives the conversation a row of its own from the start, rather than wrapping at the moment an answer begins. Below 760px keyboard hints go and the status line's small bars go with them, leaving the figures. Below 680px a bar chart row stacks its label and value above a full-width bar. Below 640px a table becomes one block per row with each cell carrying its own heading. Below 620px the headline drops to 2.25rem, skill rows stack, the page links move into the history dialog on the chat page, and the status line becomes a corridor (see Components). Touch is detected by `pointer: coarse`, not width: targets take explicit 44px minimums.

**The One Axis Rule.** The top bar, the column, the composer and the status line are centred on one axis, with equal margins left and right. The top bar and the status line share a container and so share both edges. Nothing hangs from a left edge of its own.

## Elevation & Depth

Flat. Surfaces are the sheet and one tint of it; separation is a hairline. There is exactly one shadow (`--sh-pop`), and it belongs to things that float over the sheet.

### Shadow Vocabulary
- **Pop** (`box-shadow: 0 1px 2px oklch(22% 0.015 150 / 0.08), 0 12px 32px -8px oklch(22% 0.015 150 / 0.22)`; in dark, `0 1px 2px oklch(0% 0 0 / 0.4), 0 12px 32px -8px oklch(0% 0 0 / 0.6)`): the command palette, the history dialog, toasts.

Layers are five: the aurora (-1, behind everything that sits in the sheet), status line (10), popovers (30), toasts (60), the skip link (200).

### Named Rules

**The Floats Only Rule.** A shadow means the element is over the sheet and will go away. Nothing that sits in the sheet has one. No glow, no blur, no translucent surface. The aurora is not a surface: it holds nothing, casts nothing and lies under everything.

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
- **Top bar:** the wordmark (17px, 700) at the container's left edge, three page links in the second ink at 15px, then the page's word buttons at the right. The current page is inked with a 2px underline offset 7px. No filled tab.
- **History:** a real `<dialog>` that slides in from the right (min(88vw, 24rem)), with a scrim behind it. Conversations are ruled rows: a title, a count, and a sibling delete button. The current one is weight 600.
- **Contents list:** on long-form pages, a wrapped row of second-ink links under the page head.

### Lists and rows
- **Skill rows:** six ruled rows, not six cards: a name at 600, what it does in the second ink, and the tools it runs through at 13px. Hover tints the row and extends 12px past the text on both sides so the row reads as one target.
- **Fact lists:** term in the second ink at the left, value at the right, a hairline under each, the full width of the column. The headline figures of a benchmark run are the same list with the numeral given weight.
- **Command palette:** the one floating panel (8px corners, hairline border, pop shadow). Rows are a fixed 36px (48px on touch), the list snaps to whole rows, and the highlighted command's description is set in full beneath the list.

### Cards / Containers
There are none. A **panel** is a block with 16px above it and a hairline on top. A **caveat** is a ruled note: a caution-coloured mark, then words in ink.

### Thread
- **Question:** set as a heading (20px, 600). No bubble; weight and size say whose line it is. A long paste is set as body text and folded to five lines.
- **Answer:** body text at the column measure, straight on the sheet. While it streams, a 2px live-coloured caret marks the writing position and the newest words fade up behind it.
- **Cost line:** under each finished reply, 13px in the second ink: tok/s, time to first token, tokens, time to answer, each numeral fixed-width in ink; then Copy and Ask again.
- **Route line:** one line of 13px text above a reply, steps parted by hairlines. A step is drawn while it runs, in the live colour with the word "running", and afterwards only if it was a routing decision worth knowing.
- **Notices:** a line in the thread with a mark. Queue notices take caution; an error is the one tinted block (8% fault in the sheet), with a Try again button.

### Tool figures
A tool result is a `<figure>` between two hairlines: a 13px caption, then what the tool returned. No box, no fill, no border colour. The caption carries a small drawn icon, the tool's name in ink at 600, a qualifier in the second ink ("exact", a place, a domain), and how long it took at the right end in fixed-width numerals. A number that is the answer is set at 32px. A failed tool is shown failing: icon and name in the fault colour, the message as ordinary text.

**The Caption Rule.** A finished tool is named, qualified and timed only by its figure's caption. The route line drops the step once the figure exists, and the cost line does not count it again.

### Charts
- **Bars:** a 20px track in the sheet tint, the bar in ink, square at the baseline with a 4px data end; label at the left, fixed-width value at the right.
- **Ordered pair:** p50 in ink, p95 in `bar-step`.
- **Range strip:** a hairline axis with 8px bands for the reference machines, which recede (second ink mixed 45% into the sheet). This server is a row of its own that holds only what was measured here, as an open ring; no reference row is claimed for it.
- **Line:** a 2px ink series over a 7% ink area, hairline grid, 13px labels.
- **Memory budget:** a 28px stacked bar with 2px gaps in `c1`..`c4`, and a legend that names every segment with its value. It is an estimate and is introduced as one; the process's measured resident size is printed beneath it, and in mock mode a sentence says the two are not expected to agree.
- **Tables:** right-aligned fixed-width figures, a proportional first column, hairlines under rows and a second-ink rule under the head.

### The status line (signature)
One row along the bottom edge of every page, like an editor's: sheet-tint ground, a hairline above, 13px in the second ink, at least 2.5rem tall, on the same centred container as the top bar.

- **Left, the machine, and it is live:** the state (an 8px dot and a word at 600), the model and its quantisation, then two figures measured on the server and repainted about once a second: the cores this process can use, with one small column per core filled to its load and the total as a percentage; and the machine's memory in use against its total, with a thin meter. Past eight cores the columns become one horizontal bar. A bordered `mock` tag appears when answers are canned and is never hidden. These are the whole machine's figures, which the site shares with others; a tooltip on each says so and gives the app's own share.
- **Right, the conversation, anchored to the right edge:** context use on the chat page (count, limit, a 48px by 4px meter). Each figure sits in a slot as wide as its largest value, so a number that ticks changes in place.
- **Short of room:** the row gives way in a set order. The model's name goes first, cut with an ellipsis and keeping the whole of itself in its title; only then does the trace narrow, from 6rem to no less than 4rem, which is still enough to show a shape. Nothing else moves and nothing runs into its neighbour.
- **States:** ready in ink; starting, loading, queued and busy in caution; model unavailable in fault; working, reading and writing in the live colour, as is a result held for a tab that was not being watched. "Busy" means the engine is answering someone else, which explains cores at full load beside an idle conversation. The word slot is wide enough for the request states, so they swap in place; a longer word widens it. While waiting, the dot breathes (1.4s) and a seconds count, with the queue position when there is one, appears on the right. Once tokens arrive the dot holds still and the figures do the moving.
- **In flight:** on the first token, tok/s with a live-coloured trace (the last twelve seconds, newest sample dotted) and time to first token join the right side. The rate is measured four times a second; the figure eases to each measurement and the trace scrolls at display rate, drawn a quarter of a second behind now so its head glides from one sample to the next. Every point on it is a measured sample or lies on the straight line between two. While this visitor's request runs, the core columns take the live colour: that load is the request. The context meter turns caution above 85%, the memory meter above 90%.
- **Sampling:** as soon as the page loads, then once a second while a request is running and every three seconds otherwise; not at all in a hidden tab. Load is a difference between two readings, so a server nobody has asked for a while answers the first request with no load figure; the page asks again 600ms later instead of waiting out the interval. The figure's slot is as wide as its largest value from the first paint, so nothing moves when it arrives.
- **Narrow:** below 1264px the chat page's conversation figures take their own row, context first and flush with the left edge. Below 620px the line is a corridor of one row: the state, then as much as fits of, in this order, the mock tag, the rate while the model writes, CPU load (as "CPU 12%"), memory used and total, and context use. The row wraps and only its first line is shown, so what does not fit is dropped whole, last first: memory before CPU load, when the mock tag or a long state word needs the room. Everything, with the model's thread count, the app's own memory, the limit on the service and the load average, is behind a "Details" word button pinned to the right end.

**The Say It Once Rule.** What the machine is and what state it is in are here and only here. What a request is costing is here only while the request is in flight; when it ends, the figures leave the line and are printed under the reply they belong to. A figure with nothing to report is absent, not a dash and not a zero.

**The Measured Rule.** Nothing on this line is a configured description of the machine. Core count, load and memory are read from the host while the page is open. If a figure cannot be read on a given host it is left off. The same holds for prose on the other pages: a sentence that names the machine's size prints the measured figure, a claim about what would or would not fit is worked out against it, and an estimate is called an estimate. In mock mode timings are of a fake stream: they are not stored, and the Benchmark page opens with a ruled note saying so.

### The aurora
The page's one luminous thing: a strip of light in the live colour that rises from the top edge of the status line, across the full width of the window, while this visitor's request is being read or written. At no other time is it on the page.

- **What drives it:** the same phase the status word shows, from the same call, so the two cannot disagree. Only two phases light it: reading, when the model has the prompt, and writing. The moment before that, when the request is sent and the server has not yet said whether it is running or queued, lights nothing, and neither does queued, which is someone else's work. While the model reads it holds a low, slow swell. Once tokens arrive its brightness and reach follow the measured tokens per second as a share of the fastest steady rate this page has seen (never less than 1 tok/s), so a machine writing at its own full speed is at full brightness whether that is six tokens a second or sixty; a rate counts as steady once an answer has been writing for 2.5 seconds, and until then the rate is measured against itself. Each token adds a small lift. When the answer ends it fades over about two seconds and the element is hidden, not left transparent, and stops asking for frames.
- **Where it sits:** fixed to the window, 14rem tall (10rem below 620px), a child of the body behind everything: under the thread, the composer and the palette, over nothing but the sheet's own colour. It cannot cover a control or take a pointer event.
- **Ceiling:** no pixel carries more than 24% of the live colour, on either sheet; the shader clamps to it after dithering. Composited at that ceiling the second ink reads 4.88:1 on the light sheet and 4.60:1 on the dark one, and the Stop label 4.76:1 and 4.61:1. On the dark sheet the range below the ceiling is lifted (a power of 0.55), because a faint green on near-black is close to no green at all; the ceiling itself does not move.
- **How it is drawn:** a WebGL2 fragment shader on a transparent canvas, at half resolution with the pixel ratio capped at 1.5, eased by elapsed time so it runs the same at 60 and 120 frames a second. The light is broad curtains, not bands: its noise takes each lattice value from integer arithmetic, so neighbouring pixels never differ by more than two or three levels in 255. The shader compiles when the page is idle, never on the first send. Without WebGL2, or until the shader is ready, a CSS strip of the same colour and ceiling whose opacity is the same energy. Under reduced motion, a still, faint tint for as long as the request runs.

**The One Light Rule.** There is one gradient and one glow in the system, and this is it. It is made of the live colour and governed by the Live Rule: nothing in progress, no light. Nothing may borrow it: no glow on a button, no gradient on a surface, no second effect.

### Motion
Motion explains a change, acknowledges an action or reports work in progress; nothing moves for decoration. Durations are tokens: 120ms for feedback (hover, press, colour), 200ms for a routine change of state, 320ms for something crossing part of the screen, 480ms for an entrance or a chart drawing itself. Arrivals decelerate on `cubic-bezier(0.16, 1, 0.3, 1)`; exits are faster than entrances.

- **The arrival.** Come to from outside (a typed address, a link from elsewhere, a reload), a page assembles once in reading order: headline, what it is, where to type, what to try, each rising 10px out of nothing, 50ms apart. It does not replay on New chat or when moving between the site's own pages.
- **The first send.** The composer travels from under the headline to the foot of the sheet. This is the authored move.
- **Between pages.** The top bar and the status line are the same on every page, so they hold still and only the sheet between them changes (cross-document view transitions; other browsers simply navigate).
- **The status line.** The state word slides when the state changes: the old word leaves upward, the new one comes up from below. Core columns and meters ease between samples. The dot breathes while waiting.
- **Choosing.** One rule slides under the chosen persona instead of an underline vanishing from one word and appearing under another. Send becomes Stop with the word seen to change.
- **Lists.** History rows arrive one after another, once per opening. A skill row's route leans the way it runs under the pointer. The command palette arrives as a panel; its rows, rebuilt on every keystroke, do not.
- **In flight.** The rate eases between measurements, its trace scrolls, and the aurora rises with the rate (see The status line and The aurora). These run on one shared frame loop that exists only while something is subscribed to it; at rest the page asks for no frames.
- **The thread.** Questions, route steps and tool figures enter from just below (6 to 8px, 240ms). An answer is inked in: each stretch of text fades up from nothing over 280ms as it arrives, opacity only, so nothing shifts the line it lands in. The block being written is rebuilt ten times a second, so the fade is carried across rebuilds rather than restarted, and across the final clean render too: the last words finish fading before their wrappers are removed.
- **Charts.** Bars grow from the baseline, the memory budget fills segment by segment and the history line is drawn left to right, each when scrolled to and once only.

Bars and meters animate `scaleX` or `scaleY`, never width or height. Every entrance is written from the element's resting state, so a failed animation leaves the content in place. Under reduced motion the loops stop, travel is removed, charts are shown drawn, the rate and its trace step from sample to sample, the aurora holds still and an answer's text simply appears, while colour and opacity transitions stay so the interface still acknowledges input.

## Do's and Don'ts

### Do:
- **Do** centre new content and stop it at the column measure (42rem, on every page). What needs more width breaks out symmetrically, never to one side.
- **Do** keep the status line on the same container as the top bar, on every page.
- **Do** keep `live` for what is in progress: the state dot and word, the running rate and its trace, the caret, a running step, a progress bar, the aurora.
- **Do** keep the aurora under its ceiling and behind the content, and check the second ink over it if either changes.
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
- **Don't** use gradients. The aurora is the one exception.
- **Don't** use glass, backdrop blur or translucent surfaces.
- **Don't** use glow, or add a second light. The aurora is the one exception, and only while an answer is in flight.
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
