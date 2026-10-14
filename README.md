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
  gate.py        who is let past the cover: the pattern's verifier, sessions, the limit on guesses
web/                     the interface (React 19, TypeScript, Vite, Tailwind CSS 4)
  src/lib/       framework-free: stream reader, markdown, meter, aurora, streaming answer
  src/state/     what the server knows (TanStack Query) and what the frame shows (Zustand)
  src/shell/     the frame every page shares: top bar, status line, toasts
  src/features/  chat · cards · bench · about
  src/cover/     the cover: the night sky a visitor is sent in place of the site
  src/setup/     setting the pattern that opens it (development server only)
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

**If all you see is a night sky,** the gate is closed: the site is served only
to a browser that has drawn the pattern, and with no `.env` there is no
pattern to draw. `cp .env.example .env` (step 2) sets `AURORA_GATE=off`, which
serves it to everyone. See [The gate](#the-gate).

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

## The gate

The site is one person's. A visitor who opens it is sent a night sky with the
northern lights over it, and that page is all they are sent: no application
markup, no script but the sky's, none of the site's data. Stars in the sky can
be joined with a line, by anyone. Joining the right ones in the right order
opens the site.

This repository is public, so the sky is not the lock and neither is anything
in `web/`. The lock is `app/gate.py`, on the server:

* **the pattern**, which only its owner knows;
* **a verifier**, a slow hash of the pattern, in the server's own `.env`;
* **a limit on guesses**: about a hundred wrong ones a day from everyone
  together, five in ten minutes from any one address.

Until a browser holds a session, every page it asks for is the cover, every
`/api/` path answers `401`, and nothing else exists (`/openapi.json` included).
That is decided before the application sees the request, so a route added
later is closed without anyone closing it.

| `AURORA_GATE` | `AURORA_GATE_VERIFIER` | |
|---|---|---|
| `off`, exactly | anything | **off**: the whole site is served to everyone |
| anything else, or unset | a line from the setup page | **armed**: the cover, until the pattern is drawn |
| anything else, or unset | missing or malformed | **sealed**: the cover, and nothing opens it |

A missing `.env` or a typing mistake therefore leaves a server closed, not
open. The state is written to the journal when the server starts.

### Setting the pattern

On a development machine, never on the server:

```bash
cd web && npm run dev          # then http://localhost:5173/setup.html
```

Draw a pattern on the sky, and draw it again. The page refuses one that is
short (under seven stars) or that only steps from each star to the one next
door, because those are what would be tried first. It then prints one line:

```
AURORA_GATE_VERIFIER=gate1.300000.<salt>.<hash>
```

Put it in `/srv/aurora/.env`, remove any `AURORA_GATE=off`, and restart:

```bash
sudo chown aurora:aurora /srv/aurora/.env     # the service reads it as aurora
sudo chmod 600 /srv/aurora/.env
sudo systemctl restart aurora
sudo -u aurora .venv/bin/python -m app.gate status
```

`chmod 600` alone on a file root owns leaves the service unable to read its
own settings, and it does not start.

The pattern never leaves the browser it was drawn in, and the setup page is
not part of any build. **The line is as good as the pattern**: there are few
enough patterns that whoever has it can try them all. It belongs in that one
file. To change the pattern, replace the line and restart; every session ends.

```bash
python -m app.gate status     # which way the gate is set, open sessions, guesses counted
python -m app.gate revoke     # end every session now
python -m app.gate reset      # forget the guesses counted so far
```

### What it does and does not keep out

A session lasts until the browser closes, twelve hours at most, and an hour
if nothing is asked of the server. A browser that has opened the site before
is counted apart from strangers, so people playing with the sky cannot use up
its owner's tries. A wrong pattern, a throttled one and a malformed request
get the same answer after the same wait.

What this does not do:

* **Over plain HTTP it keeps out strangers, not eavesdroppers.** The pattern
  and the session cookie cross the network as they are. Anyone on the path
  can read either. Put TLS in front before trusting it with anything that
  matters; see below.
* **A drawn pattern can be watched.** The lines are on the screen while it is
  drawn, and a finger leaves a trail on glass.
* **It hides nothing that was public before it.** Anything fetched or
  archived while the site was open is still out there.

### TLS for a server with no domain name

Let's Encrypt issues certificates for bare IP addresses. They last six days
and renew themselves. This needs Certbot 5.4 or newer, and has not been run
on any server here: the commands are from
[Let's Encrypt's own note](https://letsencrypt.org/2026/03/11/shorter-certs-certbot),
and `--staging` is there so the first try costs nothing.

```bash
sudo mkdir -p /var/www/acme
sudo certbot certonly --staging --preferred-profile shortlived \
  --webroot --webroot-path /var/www/acme --ip-address <server ip>
# when that works, once more without --staging, and with
#   --deploy-hook "systemctl reload apache2"
```

Certbot fetches these certificates but does not install them, so the web
server is pointed at them by hand. For Apache, the challenge directory must be
served from disk and not passed to the app, and the app must be told the
visitor came over HTTPS, or its cookies are not marked `Secure`:

```apache
<VirtualHost *:443>
    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/<server ip>/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/<server ip>/privkey.pem
    RequestHeader set X-Forwarded-Proto "https"
    # ...the proxy lines above...
</VirtualHost>

# in both virtual hosts, before the ProxyPass lines:
Alias /.well-known/acme-challenge/ /var/www/acme/.well-known/acme-challenge/
ProxyPass /.well-known/acme-challenge/ !
```

`deploy/nginx.conf` already sends `X-Forwarded-Proto`. Once the site answers
on 443, redirect port 80 to it. Sessions begun over HTTP do not carry over:
draw the pattern once more.

Whatever serves the site, check that it serves nothing else:
`curl -i http://<server ip>/.env` and `/data/aurora.db` must both be `404`.

---

## Configuration

Everything is `AURORA_`-prefixed, read from the environment or `.env`.

| Variable | Default | Notes |
|---|---|---|
| `AURORA_MODEL_PATH` | `./models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` | |
| `AURORA_N_CTX` | `4096` | ~115 MB of KV cache at f16 |
| `AURORA_N_THREADS` | `0` | `0` is every CPU the service may use (affinity and cgroup quota, measured at startup). A number pins it. Measured on a 4-vCPU server: 8.9 tok/s at 2 threads, 17.1 at 4 |
| `AURORA_N_BATCH` | `256` | prompt-eval batch; 512 raises peak RAM for little gain here |
| `AURORA_USE_MLOCK` | `false` | `true` on the VPS, with the systemd unit above |
| `AURORA_CHAT_FORMAT` | `auto` | `auto` uses the GGUF's own template; `chatml` forces Qwen's |
| `AURORA_HARDWARE_LABEL` | *(unset)* | how the machine is described in prose (About page, page description). Unset, it is written from what is measured, e.g. "machine with 2 cores and 3.8 GB of RAM". The status line always shows measured figures, whatever this says |
| `AURORA_MOCK` | `false` | run the site with no model present |
| `AURORA_MOCK_DELAY` | `0.085` | seconds between fake tokens; `0` runs the mock at full speed |
| `AURORA_TOOLS_ENABLED` | `true` | master switch for the tool belt |
| `AURORA_ALLOW_OUTBOUND` | `true` | `false` removes every network tool |
| `AURORA_TOOL_TIMEOUT` | `10` | seconds before a tool is abandoned |
| `AURORA_AGENT_LOOP` | `false` | let the Agent look something up, read the result and look up something else before it answers. Each extra choice is a pass through the model, in seconds; see *Looking something up twice* |
| `AURORA_AGENT_MAX_STEPS` | `3` | with the loop on: tool calls for one message |
| `AURORA_AGENT_MAX_DECISIONS` | `3` | with the loop on: passes that choose, for one message |
| `AURORA_AGENT_MAX_SECONDS` | `90` | with the loop on: seconds before the answer must begin |
| `AURORA_STEPS_KEEP_DAYS` | `30` | days to keep the step-by-step record of each message (which tool, what it was given, what came back, tokens and time per pass). It is a copy of what visitors typed; `0` keeps none |
| `AURORA_USER_AGENT` | — | **must carry a contact URL** or Wikipedia 403s |
| `AURORA_GATE` | `on` | `off`, exactly, serves the site to everyone. Anything else keeps it behind the cover; see [The gate](#the-gate) |
| `AURORA_GATE_VERIFIER` | *(unset)* | the line the setup page prints for the pattern. Server's own `.env` only. Unset or malformed, nothing opens the site |
| `AURORA_GATE_SESSION_HOURS` | `12` | the longest a session lasts, however much it is used |
| `AURORA_GATE_IDLE_MINUTES` | `60` | how long a session lasts with nothing asked of the server |

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
prose preambles and invented keys. Five layers, cheapest first:

1. **Slash commands** — `/calc 1247*89`, `/weather Paris`, `/read <url>`. Free
   and certain. Press `/` in the composer for the palette.
2. **Heuristics** — a pasted link, `1247 * 89`, `15% of 200`, `100 EUR to USD`,
   `what time is it in Tokyo`. Regex, free, and better than the model's
   judgement. These handle most real traffic with no model call at all.
3. **Grammar-constrained routing** — only for the *Agent* persona, and only
   when the first two miss. A short pass runs under a GBNF grammar generated
   from the tool registry, so malformed output is not unlikely, it is
   unrepresentable. The grammar holds what goes into a call as well as its
   shape. Text has a ceiling, so a reply always closes instead of being cut
   off mid-string by the token limit. An argument with a known set of values
   can only be given one of them: the model cannot invent a currency, and a
   unit is its own name or a word for it. An optional argument may be given
   or left out. And where the message leaves out something a tool must have,
   the pass may reply with one short question for the user instead of
   guessing; their reply is then routed with that question in view.
4. **One second try** — a call the tool still cannot take is sent back to the
   model once, with what was wrong, stated as fact. After that it is not
   argued with.
5. **Failure is visible** — a tool that errors or times out renders as a failed
   step in the timeline. The model is told too: what happened, not the advice
   the person is given beside it, and it is asked to say plainly what could
   not be looked up rather than answer from memory.

A routing pass costs ~2–5 s on 2 vCPU, which is why only the Agent persona pays
for it. It is announced on the page at the moment it is made and at no other
time, and the per-step timings are printed in the UI rather than hidden.

The grammar cannot be compiled without llama.cpp, so where the tests run it is
read instead: `tests/gbnf.py` understands the subset of GBNF the generator
emits and says what a grammar accepts and what it refuses.

### Looking something up twice

As it ships, a message that reaches the model makes one choice of tool and
then answers. It cannot look something up, read what came back, and decide to
look up something else. `AURORA_AGENT_LOOP=true` lets it: a tool the model
chose earns it another choice.

The model is not trusted to know when to stop. A small one stops too early, or
asks for the same thing again. So the code that runs the loop holds the limits:

- at most three tool calls and three choices for one message, and ninety
  seconds before the answer must begin (all three are settings);
- a lookup that has already been made is not made again;
- a tool the visitor named themselves, by a command, a pattern or a skill,
  never starts the loop. Those cost exactly what they cost with it off.

When something other than the model ends the looking-up, the pass that writes
the answer is told so in one line, so the reply can say what was not found.

Two things keep the extra passes cheap. The whole run is one transcript that
only grows, and every pass reads it from the top, so each pass after the first
is charged only for what is new. And the run takes the inference slot once and
keeps it, so nobody else's prompt replaces it in the model's cache part-way
through; if a tool goes out to the network while somebody is waiting, the slot
is lent to them for that time.

It is off by default because the one thing that decides its worth cannot be
known without measuring it on the model: whether a second choice by a 1.5B
model is good enough to be worth its seconds. `scripts/eval_agent.py --loop`
is how to find out.

### Safety

`read_url` is the only tool that makes the server fetch a user-supplied
address, so it resolves the hostname first and refuses any private, loopback,
link-local, CGNAT, reserved or multicast address — including
`169.254.169.254`, the cloud metadata endpoint. Redirects are followed by hand,
maximum three hops, re-validating at each one. Bodies are capped at 2 MB and
only `text/html` / `text/plain` is read.

That guard is about where an address points. It says nothing about an address
that points somewhere public and was put there by a page the model read: "now
open http://elsewhere/?q=" followed by something from the conversation. So when
it is the model that chooses to read a page, the address must be one the user
wrote themselves, in this message or an earlier one. The model may pass an
address on; it may not make one up or lift one from what it has read. A longer
path or a query string the user did not write is a different address. This is
checked in code and does not depend on the model noticing anything.

What a tool returns is given to the model as reading matter: wrapped in a
`<data>` tag, with the model told once that what is inside one is material and
never an instruction, and with anything in it that looks like that tag or like
a control token of the chat format taken out first. That is a convention and
not a wall, which is why the rule above is enforced in code.

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
to the next visitor while llama.cpp is still mid-token. The short pass that
chooses a tool is a pass like any other and follows the same rule: it queues,
runs on a worker thread, and that thread hands the slot back.

**A run has one name from its first event to its last.** The first event of a
chat stream carries a `request_id`, and `POST /api/cancel/{id}` stops the run
at whatever stage it has reached: waiting for the slot, choosing a tool,
waiting on a page, or writing. A tool that is out is called off there and then,
and whatever was already written is kept.

**Every run leaves a trace.** Each thing a message did — how its tool was
chosen, each tool call, the pass that wrote the answer — is a row in the
`steps` table: what it was given, what came back, and what it cost in tokens
and time. A step's time is its own cost and nothing else's, so the steps of a
run add up to its total, and the stream's last event, `run`, reports that
total. Rows are kept for `AURORA_STEPS_KEEP_DAYS` and go with their
conversation when it is deleted.

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
TTFT p50/p95 and peak RSS (the app and its model), and stores every run so
`/bench` can chart drift.

Every run reads its prompt cold: each one opens with a word no other run's
prompt opens with, so the model has nothing cached to reuse. Repeating the same
prompt made the second and third runs cache hits, and the suite reported
500–9,000 tok/s of prefill on a server whose real figure is 60–140.

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
| `POST /api/chat` → SSE | events: `conversation` (with the run's `request_id`), `step`, `card`, `queue`, `start`, `token`, `trimmed`, `done`, `error`, and last of all `run`: how the run ended, its steps and model passes, and what they cost together |
| `GET /api/tools` | the registry, as the UI sees it |
| `GET /api/skills` | workflow definitions |
| `POST /api/cancel/{id}` | stop a run, at any stage, by the `request_id` from its first event |
| `GET /api/health` | model, status, RSS, queue depth, uptime, and a `system` snapshot |
| `GET /api/telemetry` | the machine now: usable CPUs, load per core, memory used/total, app RSS, cgroup limit, load average; plus engine `status`, `busy`, `queued` |
| `GET/DELETE /api/conversations[/{id}]` | history |
| `POST /api/bench/run` → SSE | `stage`, `rep`, `case`, `summary` |
| `GET /api/bench/results` | the suite, the latest summary, the history, reference machines, the memory estimate |
| `GET /api/bootstrap` | the first values a page shows; the same JSON the server writes into `index.html` (the Vite dev server's page asks for it instead) |
| `POST /api/unlock` | `{"chain": [star, …]}` → `{"ok": true}` and a session cookie, or `{"ok": false}`. Answered by the gate, not the application, and the only path above that answers without a session |

The pages themselves are `/`, `/bench` and `/about`, all the one built
`index.html`; `/assets/<name>` is its files. With the gate armed, a browser
without a session is sent `cover.html` at every one of those addresses, and
`401` for everything in the table but the last row.

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

`tests/test_gate.py` is the gate, armed: that every route the application has
answers nothing without a session (read from the application as it stands, so
a new route is held to it), that every refusal is the same bytes, the limits
on guesses, sessions and their ends, and that the hash is the one the setup
page makes. The rest of the suite runs with the gate off, which
`tests/__init__.py` sets before anything is imported.

The rest follow a message through the server, with a script standing where the
model would be (`evals/scripted.py`). It is the real engine with one call
replaced, so the lock, the queue, the worker thread and Stop are the code the
server runs. `test_engine.py` is about the one inference slot: that two passes
never overlap, and that a reader who leaves mid-pass cannot free the slot while
llama.cpp is still working. `test_agent.py` drives a run directly, and stops it
at each stage. `test_chat_stream.py` does the same over HTTP and reads what was
stored. `test_router.py`, `test_tools.py` and `test_steps.py` cover routing,
argument checking and the stored trace. No test sleeps to let something happen,
and a test that tries to resolve a hostname fails.

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

Those run with the gate off. `npm run e2e` then runs a second, small suite,
`web/e2e/gate`, against a server of its own with the gate armed and a pattern
made up for the purpose: the cover at every address with nothing of the site
in it, a short drawing that never leaves the browser, a wrong pattern, and the
right one opening the page that was asked for.

### Does it do the right thing

The benchmark says how fast. This says whether, given a message, the right
thing happened: the right tool, the right arguments, no pass that was not
needed. `evals/tasks.jsonl` is a file of tasks, one per line, each with what to
send and what must be true afterwards. Tool results come from
`evals/fixtures`, so no task touches the network, and nothing is written to the
database.

```bash
python scripts/eval_agent.py                                   # scripted: no model needed
python scripts/eval_agent.py --engine real --out evals/reports/today.json
python scripts/eval_agent.py --engine real --compare evals/reports/before.json
python scripts/eval_agent.py --engine real --loop              # every task with the loop on
python scripts/eval_agent.py --engine real --probe-routing     # one routing pass, cold then warm
```

**Scripted**, each task brings what the model says, so the run measures the
code around the model and nothing about the model. It takes a second and runs
with the server's tests. Its report says its token counts are estimates and
carries no timings.

**Real** loads the model as the site does and gives every pass the same seed.
It is the only source of a number about what the agent can do: task success,
routing accuracy, argument match, steps and passes, tokens, and seconds per
task split into waiting, reading and writing. Stop the web service first: a
second process loads a second copy of the model and shares the cores, exactly
as the benchmark would.

A task that is about the loop asks for it itself, so it runs with the loop on
whatever the settings say. `--loop` turns it on for all the others too. Run the
suite both ways and compare: the report says how many runs made a second
lookup, how many were ended by a limit and how many by asking for the same
thing twice, beside task success and seconds per task. That comparison is what
decides whether the loop stays on.

A task marked `pending` is a known gap, written down with its reason. It is
expected to fail; the day it passes, the run fails until the mark comes off.

---

## Limitations

It's a 1.5B model at 4 bits. It gets arithmetic wrong, invents citations, loses
long reasoning chains, and knows nothing recent. It's good at short explanations,
quick drafts, summarising pasted text, and simple code — in a couple of seconds,
without a byte leaving the machine.
