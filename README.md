# Aurora

A web front-end that makes a tiny local model look intentional.

`Qwen2.5-1.5B-Instruct` at `Q4_K_M`, served from a 2 vCPU / 4 GB VPS with no GPU,
no API key and no upstream provider. The performance story isn't hidden — it's
the feature: live tokens/sec while the answer streams, the machine's cores and
memory measured and shown as they are used, time-to-first-token under every
reply, and a benchmark page that measures the machine it's running on and plots
it against reference hardware.

It also gives the model a tool belt — exact arithmetic, a clock, Wikipedia,
weather, currency and a URL reader — because those are precisely the things a
1.5B model gets wrong, and they are fixable without a bigger model.

```
app/                     the server (Python, FastAPI)
  main.py        the API: SSE chat, tools, skills, benchmark, history, the page routes
  web.py         serves the built interface and writes its first values into the page
  llm.py         one Llama instance, one lock, honest metrics, grammar routing
  telemetry.py   the machine, measured: usable CPUs, per-core load, memory, cgroup limits
  router.py      slash commands -> heuristics -> grammar-constrained routing
  skills.py      one-click workflows built on the tools
  tools/         base (registry + GBNF) · offline · web · reader (SSRF guard)
  bench.py       the benchmark suite + reference data
  personas.py    five presets (system prompt + sampling profile)
  db.py          SQLite (WAL): conversations, messages, tool_calls, runs
web/                     the interface (React 19, TypeScript, Vite, Tailwind CSS 4)
  src/lib/       framework-free: stream reader, markdown, meter, aurora, streaming answer
  src/state/     what the server knows (TanStack Query) and what the frame shows (Zustand)
  src/shell/     the frame every page shares: top bar, status line, toasts
  src/features/  chat · cards · bench · about
  src/styles/    the design tokens, the Tailwind theme that names them, the status line
  src/assets/    Recursive, self-hosted (SIL OFL 1.1)
  e2e/           browser tests (Playwright) and the database they run against
  dist/          the build; not on master, only on the release branch
tests/                        the server's unit tests (standard library unittest)
scripts/bench_cli.py          headless benchmark → JSON
scripts/publish_release.sh    what CI runs to publish a build to the release branch
deploy/                       systemd unit + nginx site
.github/workflows/ci.yml      test, build, publish
```

Two branches matter. **`master`** is the source. **`release`** is the same
source with the interface already built in `web/dist`; CI makes it from every
commit on `master` that passes its tests. A server runs `release` and never
needs Node.

---

## Quick start

### 1. Get the model

```bash
mkdir -p models
curl -L -o models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf \
  https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

About 1.0 GB. Any GGUF with a chat template works — point `AURORA_MODEL_PATH` at it
and update `AURORA_MODEL_LABEL` / `AURORA_MODEL_QUANT` so the UI stops lying.

### 2. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip

# Build llama.cpp for this CPU. -DGGML_NATIVE=ON is worth real tokens/sec.
CMAKE_ARGS="-DGGML_NATIVE=ON" pip install --no-cache-dir llama-cpp-python

pip install -r requirements.txt
cp .env.example .env
```

On a 2 vCPU box the llama.cpp build takes 5–15 minutes and wants ~1 GB of free
RAM. If it gets OOM-killed, add swap first (`fallocate -l 2G /swapfile`), or
install a prebuilt wheel from the llama-cpp-python releases page.

### 3. Get the interface

The interface is built into static files that the server sends. Either take
them already built, which is what a server should do and needs no Node:

```bash
git fetch origin
git checkout release
```

or build them from source, which needs Node 22.22 or newer:

```bash
cd web
npm ci
npm run build      # writes web/dist
cd ..
```

Started without `web/dist`, the server still answers the API and says on every
page that the interface has not been built, with these two commands.

### 4. Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

**One worker, always.** A second worker loads a second copy of the weights and
puts the box straight into swap.

### Preview the UI without the model

```bash
AURORA_MOCK=true uvicorn app.main:app --port 8000
```

Mock mode fakes a generator at ~11 tok/s — the whole site works, including the
benchmark, so you can develop the front-end anywhere. Its timings measure a
sleep, not a model, so mock mode writes nothing to the `runs` table (neither
chat nor benchmark runs) and the Benchmark page says so at the top. On Windows:

```powershell
$env:AURORA_MOCK="true"; .venv\Scripts\python -m uvicorn app.main:app --port 8000
```

### Working on the interface

Two processes: the server (mock mode is enough) and Vite, which serves the
page from source with hot reload and passes `/api` through to the server,
streams included.

```bash
AURORA_MOCK=true uvicorn app.main:app --port 8000      # one terminal
cd web && npm run dev                                   # another: http://localhost:5173
```

`AURORA_API` points Vite at a server somewhere else; `AURORA_WEB_PORT` moves
Vite off 5173. In development React mounts every component twice on purpose,
which is how a request sent from the wrong place shows up as two.

| In `web/` | |
|---|---|
| `npm run dev` | the dev server |
| `npm run build` | the production build, into `web/dist` |
| `npm run typecheck` | TypeScript, no output |
| `npm run lint` / `npm run format` | Biome: check, or check and fix |
| `npm test` | unit tests (Vitest) |
| `npm run e2e` | browser tests (Playwright); build first |

---

## Deploying

A server runs the `release` branch: the same source as `master`, with
`web/dist` committed. Nothing on the server builds anything.

```bash
git clone --branch release https://github.com/Qusayy/OpenSourcePersonalChat.git /srv/aurora
```

A server that was cloned before `release` existed switches to it once. `.env`,
`data/`, `models/` and `.venv/` are untracked and are left alone:

```bash
cd /srv/aurora
git fetch origin
git checkout release
pip install -r requirements.txt
sudo systemctl restart aurora
```

After that an update is what it always was, on `release`:

```bash
git pull && pip install -r requirements.txt && sudo systemctl restart aurora
```

**Never pull `master` on a server.** It has no `web/dist`, and the site would
come up saying the interface has not been built. `release` only ever moves
forward, one commit per published build, so `git pull` there never has
anything to merge.

How `release` gets made: on every push to `master`, `.github/workflows/ci.yml`
type-checks, lints, tests and builds the interface, runs the server's tests,
drives the built interface in a browser against the real server, and then runs
`scripts/publish_release.sh`, which adds one commit to `release`: that
`master` commit's tree plus `web/dist`, on top of the previous release.

```bash
sudo cp deploy/aurora.service /etc/systemd/system/
sudo cp deploy/nginx.conf /etc/nginx/sites-available/aurora
sudo ln -s /etc/nginx/sites-available/aurora /etc/nginx/sites-enabled/
sudo systemctl daemon-reload && sudo systemctl enable --now aurora
sudo nginx -t && sudo systemctl reload nginx
```

> **The one thing that breaks this deploy:** nginx must have `proxy_buffering off`
> and `gzip off` on `/api/chat` and `/api/bench/run`. With buffering on, nginx
> holds every token until the response finishes, the answer lands in one lump,
> and every live metric on the site becomes decorative. `deploy/nginx.conf` sets
> it; don't "tidy" it away.

What the proxy has to know about the interface:

* **`/assets/`** is the built files, each named for its contents
  (`index-BMSTREQI.css`). They can be cached for a year, and the app sends them
  that way; `deploy/nginx.conf` serves them straight from
  `web/dist/assets/` instead. A missing one must be a 404, never the page: a
  browser holding last week's page asks for last week's file names.
* **Every other path** goes to the app, which answers `/`, `/bench` and `/about`
  with the one page (`Cache-Control: no-cache`, so a new build is picked up on
  the next visit) and anything else with that page and a 404.

**Behind Apache instead of nginx** (not tested on any server; the same three
requirements, in Apache's words): proxy everything to uvicorn; on `/api/chat`
and `/api/bench/run` send each chunk as it arrives and do not compress
(`flushpackets=on`, and no `DEFLATE` filter on those two paths), with a
timeout of at least 900 seconds, because a benchmark run holds its connection
for minutes; and no `FallbackResource` or `ErrorDocument 404` pointing at the
page, for the reason above.

```apache
ProxyPreserveHost On
ProxyPass        /api/chat       http://127.0.0.1:8000/api/chat       flushpackets=on timeout=900
ProxyPass        /api/bench/run  http://127.0.0.1:8000/api/bench/run  flushpackets=on timeout=900
ProxyPass        /               http://127.0.0.1:8000/
ProxyPassReverse /               http://127.0.0.1:8000/

SetEnvIf Request_URI "^/api/(chat|bench/run)" no-gzip
```

Other deployment notes:

* `LimitMEMLOCK=infinity` in the unit is required if you set
  `AURORA_USE_MLOCK=true`. Without it llama.cpp can't pin the weights and quietly
  falls back to pageable memory.
* `MemoryMax=3G` is an OOM guard — the service restarts instead of taking the
  whole VPS down with it.
* Keep ~2 GB of swap as a safety net. In normal operation the process should
  never touch it.
* The live CPU and memory figures need nothing extra on Linux: they are read
  from `/proc` and the service's cgroup with the standard library. On Windows
  and macOS `requirements.txt` installs `psutil` for the same job. Where a
  figure cannot be read it is left off the page rather than guessed.
* A systemd `CPUQuota=` or `MemoryMax=`, or a container's limits, are picked up
  from the cgroup: the page shows the CPUs and memory this service can actually
  use, not what the kernel can see.

---

## Configuration

Everything is `AURORA_`-prefixed, read from the environment or `.env`.

| Variable | Default | Notes |
|---|---|---|
| `AURORA_MODEL_PATH` | `./models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` | |
| `AURORA_N_CTX` | `4096` | ~115 MB of KV cache at f16 |
| `AURORA_N_THREADS` | `2` | match the vCPU count; more threads on 2 cores *loses* throughput |
| `AURORA_N_BATCH` | `256` | prompt-eval batch; 512 raises peak RAM for little gain here |
| `AURORA_USE_MLOCK` | `false` | `true` on the VPS, with the systemd unit above |
| `AURORA_CHAT_FORMAT` | `auto` | `auto` uses the GGUF's own template; `chatml` forces Qwen's |
| `AURORA_HARDWARE_LABEL` | *(unset)* | how the machine is described in prose (About page, page description). Unset, it is written from what is measured, e.g. "machine with 2 cores and 3.8 GB of RAM". The status line always shows measured figures, whatever this says |
| `AURORA_MOCK` | `false` | run the site with no model present |
| `AURORA_MOCK_DELAY` | `0.085` | seconds between fake tokens; `0` runs the mock at full speed |
| `AURORA_TOOLS_ENABLED` | `true` | master switch for the tool belt |
| `AURORA_ALLOW_OUTBOUND` | `true` | `false` removes every network tool |
| `AURORA_TOOL_TIMEOUT` | `10` | seconds before a tool is abandoned |
| `AURORA_USER_AGENT` | — | **must carry a contact URL** or Wikipedia 403s |

---

## Tools and skills

Eight tools, no API keys anywhere:

| Tool | | Network |
|---|---|---|
| `calculator` | exact arithmetic via `Fraction` — `0.1+0.2` really is `0.3` | — |
| `clock` | date and time; accepts `Europe/Paris`, `Tokyo`, `new york` or `Japan` | — |
| `convert_units` | length, mass, time, data, speed, volume, temperature | — |
| `text_stats` | words, sentences, reading time, top terms | — |
| `wikipedia` | article summaries | yes |
| `weather` | current conditions + 5-day forecast (open-meteo) | yes |
| `currency` | ECB rates (Frankfurter) | yes |
| `read_url` | fetch a page and extract its readable text | yes |

**Skills** are one-click workflows on top of them — *Summarise a link*,
*Fact-check*, *Compare two things*, *Weather brief*, *Do the maths*,
*Explain this code*. Each is a dict entry in `app/skills.py`, not a code path.

### How a 1.5B model is made to route reliably

It isn't, on its own — asked politely for JSON it produces trailing commas,
prose preambles and invented keys. Four layers, cheapest first:

1. **Slash commands** — `/calc 1247*89`, `/weather Paris`, `/read <url>`. Free
   and certain. Press `/` in the composer for the palette.
2. **Heuristics** — a pasted link, `1247 * 89`, `15% of 200`, `100 EUR to USD`,
   `what time is it in Tokyo`. Regex, free, and better than the model's
   judgement. These handle most real traffic with no model call at all.
3. **Grammar-constrained routing** — only for the *Agent* persona, and only
   when the first two miss. A short pass runs under a GBNF grammar generated
   from the tool registry, so malformed output is not unlikely, it is
   unrepresentable.
4. **Failure is visible** — a tool that errors or times out renders as a failed
   step in the timeline and the model answers without it.

A routing pass costs ~2–5 s on 2 vCPU, which is why only the Agent persona pays
for it. The per-step timings are printed in the UI rather than hidden.

### Safety

`read_url` is the only tool that makes the server fetch a user-supplied
address, so it resolves the hostname first and refuses any private, loopback,
link-local, CGNAT, reserved or multicast address — including
`169.254.169.254`, the cloud metadata endpoint. Redirects are followed by hand,
maximum three hops, re-validating at each one. Bodies are capped at 2 MB and
only `text/html` / `text/plain` is read.

The calculator parses with `ast` and a node whitelist. It never calls `eval`,
and rejects attribute access, comprehensions, lambdas and `__import__`.

Set `AURORA_ALLOW_OUTBOUND=false` to drop every network tool from the registry,
the grammar and the UI in one move.

> Wikipedia returns **403** for a User-Agent with no contact details. Set
> `AURORA_USER_AGENT` to something including your repository URL or email.

## How it works

**One generation at a time.** With two cores, two generations running side by
side finish later than the same two run back to back. The engine takes a single
lock and reports queue position over SSE, so waiting visitors see
"1 request ahead" instead of a frozen page.

**The lock is released by the generation thread**, in its own `finally` — not by
the request handler. Otherwise a client disconnecting mid-answer hands the slot
to the next visitor while llama.cpp is still mid-token.

**Tokens cross threads through a queue.** Generation runs on a plain thread and
pushes into an `asyncio.Queue`, so the event loop stays free for `/api/health`,
cancellation, and other clients' queue counters.

**The KV cache is reused.** One `Llama` instance for the process lifetime, full
conversation sent each turn — llama.cpp prefills only the new tokens, so turn 5
starts far faster than turn 1. Creating a `Llama` per request would throw that
away and re-read a gigabyte of weights every time.

**The chat template does the work the sampler can't.** An instruct model fed raw
text behaves like a base model: it rambles, repeats, and never stops cleanly. The
usual response — clamp temperature to ~0 and bolt on a repeated-sentence
detector — makes it worse, because greedy decoding is what produces loops.
Routing every turn through `<|im_start|>` / `<|im_end|>` lets the model stop on
its own token and lets sampling sit at a normal `0.7` with a light
`repeat_penalty`.

**The front-end is built ahead of time, and everything it needs is served from
this machine.** It is React 19 and TypeScript, built by Vite into one script,
one stylesheet and one font file, each named for its contents. Five packages
ship to the browser: `react`, `react-dom`, `react-router`,
`@tanstack/react-query` and `zustand`. There is no CDN, no tracker, no web font
service and no component kit. The markdown renderer is the site's own, about
150 lines in `web/src/lib/markdown.ts`, and escapes everything before producing
a tag; the icons are inline SVG, because symbols like `⧉` and `⇹` are missing
from plenty of font stacks and render as tofu boxes on somebody else's machine.
The typeface is Recursive, a variable font kept in `web/src/assets/fonts/`
under the SIL Open Font License. Its `MONO` axis sets every figure fixed-width
in the same letterforms as the prose.

**React draws the page; it does not draw every frame.** What changes many times
a second is written straight to the DOM by plain modules that React hands an
empty element and then leaves alone: the answer as it streams (settled blocks
kept, the last one rebuilt ten times a second, new words faded in), the rate
figure and its trace, the state word, the aurora. A token arriving re-renders
nothing. The server's state is TanStack Query, polled by one component and
read by the rest from its cache, so three places showing memory use cost one
request.

**The first values are in the page.** The server writes the model, the machine
as last measured, the personas, tools, conversations and the benchmark's suite
into `index.html` as one JSON element (`app/web.py`), so the first paint has
real figures with no request behind it, and no persona prompt or file path is
among them.

**Styling is Tailwind CSS 4, with the design tokens as its only vocabulary.**
The tokens stay plain custom properties, which is how the dark scheme is one
block of redefinitions; the Tailwind theme only names them, and Tailwind's own
palette, scales and breakpoints are removed. `DESIGN.md` says which parts are
written as CSS instead of utility classes, and why.

**The machine is measured, not described.** `app/telemetry.py` reads the host
while the page is open: the CPUs this process may use (scheduler affinity, then
the cgroup's CPU quota, so `CPUQuota=150%` reads as 1.5 cores), load per core
from two readings of `/proc/stat`, memory in use and in total from
`/proc/meminfo`, the process's own resident size, and the tightest
`memory.max` among the service's cgroup and its ancestors (cgroup v2, with a v1
fallback). Nothing about the machine is a configured string. The status line
polls `GET /api/telemetry` once a second while a request is running and every
three seconds otherwise, and not at all in a hidden tab; the endpoint is a few
small file reads and is rate-limited inside the sampler, so many open tabs cost
one sample.

**The status line reports generation as it happens.** One line along the bottom
of every page, centred on the same container as the top bar, carries the
server's state, the model, and the machine's cores and memory as measured: a
small column per core filled to its load, and memory in use against the total.
On the chat page it also shows how much of the context window is in use.
While a request is in flight it carries what that answer is costing: a
tokens-per-second figure measured four times a second, with a trace of the last
twelve seconds that scrolls at display rate, and time to first token. It names
each phase — queued, reading, writing — and counts the seconds through the
waits. When the answer ends the
figures leave the line and are printed under the reply they belong to, so the
same number is never on screen twice. The rolling rate is client-side
arithmetic over the token stream; time to first token is the server's own
measurement, sent with the first token, and the figures under each reply are
the server's too. An answer that finishes while the tab is in the background
stays marked until the tab is seen.

**One effect, and only while the model is working.** While an answer is being
read or written a strip of light in the page's one accent colour rises from
the status line, behind everything else on the page. Its brightness and reach
follow the measured tokens per second as a share of the fastest steady rate the
page has seen, so a slow machine at its own full speed is as bright as a fast
one, with a small lift as each token lands; it is off while a request is queued
behind someone else's, and when the answer ends it fades and is removed. It is a WebGL2 fragment shader on a transparent
half-resolution canvas (`web/src/lib/aurora.ts`), compiled when the page is idle,
with a CSS strip driven by the same value where WebGL2 is missing. Its alpha is
capped so that the page's secondary text still clears 4.5:1 over its brightest
pixel. The answer's own text fades up as it arrives rather than appearing in
slabs. The rest of the motion is small and explains something: the page
assembles once on arrival, the composer travels to the foot of the sheet on
the first send, the state word slides when the state changes, and charts draw
when scrolled to.

The page follows the visitor's light or dark setting. Under
`prefers-reduced-motion` nothing loops or travels: the effect becomes a still
tint, text simply appears, and charts are shown already drawn.

---

## Benchmarks

Run the suite from the `/bench` page, or headless:

```bash
python scripts/bench_cli.py --out bench.json
```

Three prompt sizes (~32 / ~256 / ~1024 tokens) × 128 generated tokens, 3/3/2
repetitions, one discarded warm-up. It reports prefill tok/s, generation tok/s,
TTFT p50/p95 and peak RSS, and stores every run so `/bench` can chart drift.

### Reference points

Order-of-magnitude figures for this model and quantisation under llama.cpp. They
swing widely with memory bandwidth, AVX support, and — on shared cloud — whoever
else is on the host. **The `/bench` page replaces the first row with what your
box actually did.**

| Hardware | Threads | Prefill tok/s | Generation tok/s |
|---|---|---|---|
| 2 vCPU shared cloud | 2 | ~25–60 | **~6–12** |
| 4 vCPU dedicated, AVX2 | 4 | ~60–120 | ~12–20 |
| Desktop 8-core, DDR5 | 8 | ~200–400 | ~30–55 |
| Apple M2 (Metal) | — | ~500+ | ~45–70 |

### Why those numbers

Generating a token reads **every weight once** — about 1 GB at `Q4_K_M` — so
throughput follows memory bandwidth, not clock speed:
`tokens/sec ≈ effective bandwidth ÷ model size`. That's also the whole argument
for 4-bit: a quarter of the bytes moved, roughly four times the speed. Prefill is
a batched matmul and therefore compute-bound, which is why a 1024-token prompt
costs far less than 32× a 32-token one.

### Memory budget

| Component | RAM |
|---|---|
| Weights (`Q4_K_M`, mmap) | ~990 MB |
| KV cache @ 4096 ctx, f16 (28 layers × 2 KV heads × 128 dim = 28 KiB/token) | ~115 MB |
| Compute buffers @ `n_batch 256` | ~200 MB |
| Python + FastAPI + uvicorn | ~120 MB |
| **Peak** | **~1.4 GB of 4 GB** |

A 7B model at the same quantisation is ~4.4 GB of weights alone — it wouldn't
load, and if it did, every token would come off disk.

---

## API

| Endpoint | |
|---|---|
| `POST /api/chat` → SSE | events: `conversation`, `step`, `card`, `queue`, `start`, `token`, `trimmed`, `done`, `error` |
| `GET /api/tools` | the registry, as the UI sees it |
| `GET /api/skills` | workflow definitions |
| `POST /api/cancel/{id}` | stop an in-flight generation |
| `GET /api/health` | model, status, RSS, queue depth, uptime, and a `system` snapshot |
| `GET /api/telemetry` | the machine now: usable CPUs, load per core, memory used/total, app RSS, cgroup limit, load average; plus engine `status`, `busy`, `queued` |
| `GET/DELETE /api/conversations[/{id}]` | history |
| `POST /api/bench/run` → SSE | `stage`, `rep`, `case`, `summary` |
| `GET /api/bench/results` | the suite, the latest summary, the history, reference machines, the memory estimate |
| `GET /api/bootstrap` | the first values a page shows; the same JSON the server writes into `index.html` (the Vite dev server's page asks for it instead) |

The pages themselves are `/`, `/bench` and `/about`, all the one built
`index.html`; `/assets/<name>` is its files.

```bash
curl -N -X POST localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"count to twenty"}'
```

Tokens must appear one at a time. If they arrive all at once, something between
you and uvicorn is buffering — see the nginx note above.

### Tests

Three suites. None needs a model.

```bash
python -m unittest discover -s tests -t .     # the server
cd web && npm test                            # the interface, unit by unit
cd web && npm run build && npm run e2e        # the interface, in a browser
```

**The server's** use the standard library's `unittest`. The telemetry tests
need nothing else: they cover the parsers and the sampler in
`app/telemetry.py` against fixture trees of `/proc` and cgroup files (v2, v1,
nested limits, quotas, missing files), so they run the same on any OS.
`tests/test_runs.py` checks what is kept in the `runs` table (real runs are,
mock runs are not), and `tests/test_web.py` checks how the built interface is
served: the page and its embedded values, escaping, cache headers, a path that
tries to climb out of `assets/`, a missing build, and what a 404 is for a
page, for a file and for the API. Those two need the app's requirements;
without them they are skipped.

**The interface's unit tests** (Vitest) cover the modules that are algorithms:
the markdown renderer and its escaping, the stream reader, the meter, the
streaming answer's DOM, the chart geometry, the stores, each tool card.

**The browser tests** (Playwright) drive the built interface served by the real
app in mock mode, with outbound requests switched off. `web/e2e/seed.py` fills
the test database with three conversations, one of which carries every card.
They run three ways:

```bash
npm run e2e                                             # the built files, served by the app
AURORA_E2E_INSECURE=1 npm run e2e                       # the same, not a secure context
AURORA_E2E_BASE=http://127.0.0.1:5173 npm run e2e       # against `npm run dev`
```

The second runs the page under a made-up hostname, where
`navigator.clipboard` and `crypto.randomUUID` do not exist. That is the page as
it is on `http://<server ip>`, and `localhost` hides it. On a development
machine the tests use the Chrome that is installed; CI installs Chromium.
Playwright starts the server itself, with `python` (on Windows, the
repository's `.venv`); `AURORA_PYTHON` names another interpreter.

---

## Limitations

It's a 1.5B model at 4 bits. It gets arithmetic wrong, invents citations, loses
long reasoning chains, and knows nothing recent. It's good at short explanations,
quick drafts, summarising pasted text, and simple code — in a couple of seconds,
without a byte leaving the machine.
