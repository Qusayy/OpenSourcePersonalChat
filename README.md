# Aurora

A web front-end that makes a tiny local model look intentional.

`Qwen3.5-4B` at `Q4_K_M`, served from a 4 vCPU / 8 GB VPS with no GPU,
no API key and no upstream provider. The performance story isn't hidden — it's
the feature: live tokens/sec while the answer streams, the machine's cores and
memory measured and shown as they are used, time-to-first-token under every
reply, and a benchmark page that measures the machine it's running on and plots
it against reference hardware.

It also gives the model tools — exact arithmetic, a clock, Wikipedia, weather,
currency, a URL reader, the web, the headlines, a Python sandbox and a project
of files it writes and edits — and you talk to it: it thinks, answers, and
calls a tool itself, in its own format, when it judges one is needed.

```
app/                     the server (Python, FastAPI)
  main.py        the application: the gate in front, then every group of routes
  api/           the routes: pages · chat (SSE) and what a run leaves behind · conversations · system · bench
                 frontend.py serves the built interface and writes its first values into the page
  agent/         one message, question to answer: loop · reply (one reply, in phases) · calls (the
                 model's tool-call format) · route (slash commands) · policy (what a call may do)
                 trace · style (prose or code sampling) · transcript · context (the window) · verify
  llm/           the engine: one slot, one lock, honest metrics (core); the llama-server client (server); mock
  store/         SQLite (WAL): db (the connection) · conversations · runs (steps and figures) · notes
  tools/         base (registry, arguments, dispatch), then one module per tool; read_url carries the
                 SSRF guard; write_file holds write_file and edit_file
  ops/           telemetry (usable CPUs, per-core load, memory, cgroup limits) · bench (the suite + reference data)
  gate.py        who is let past the cover: the pattern's verifier, sessions, the limit on guesses
  config.py · text.py · chatml.py · ledger.py   settings · token estimates and excerpts · the prompt as the model reads it · what a run cost
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
deploy/                       systemd units (the app, the model server), the model server's build script, nginx site
.github/workflows/ci.yml      test, build, publish
```

Two branches matter. **`master`** is the source. **`release`** is the same
source with the interface already built in `web/dist`; CI makes it from every
commit on `master` that passes its tests. A server runs `release` and never
needs Node. The job that pushes `release` builds nothing and installs nothing:
it publishes what the read-only build job made, and holds the push token only
for the push, so no package's install script ever runs where it could write to
the repository. Every action the workflow uses is pinned by commit.

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

That is the model in the app's own process, which is how it runs unless told
otherwise. On a server it can run faster in a llama-server beside the app
instead; see [The model server](#the-model-server).

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
puts the box straight into swap. With the model in a llama-server it is still
one: the slot and the queue live in the app, and a second worker would send the
server work the first one does not know about.

**If all you see is a night sky,** the gate is closed: the site is served only
to a browser that has drawn the pattern, and with no verifier there is no
pattern to draw. On a development machine, uncomment `AURORA_GATE=off` in the
`.env` you copied in step 2; it serves the site to this machine, and to no
other name unless `AURORA_ALLOWED_HOSTS` lists it. Never on a server. See
[The gate](#the-gate).

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

### The model server

On a server the model can run in a llama-server beside the app instead of in
the app's own process. Measured on the 4-vCPU server (AVX2, no AVX-512) this
site runs on, with the same `Qwen2.5-1.5B-Instruct-Q4_K_M` file and a
~1,100-token prompt:

| Where the model runs | Writing | Reading the prompt | Next turn reads |
|---|---|---|---|
| llama-cpp-python, in the app | 15.6 tok/s | 15.1 s | ~0.1 s |
| ik_llama.cpp `llama-server -rtr` | 23–24 tok/s | 13.8 s | 0.8 s |

The server is [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp), a
llama.cpp fork tuned for CPUs; `-rtr` repacks the weights for this CPU as they
load, and most of the gain is that. It also keeps a conversation's prompt
cached for models with recurrent layers, which llama-cpp-python has to re-read
on every turn. The app keeps everything that decides who runs when (the one
slot, the queue, Stop) and sends each pass over loopback.

As your own admin user, not as `aurora`, which has no sudo:

```bash
cd /srv/aurora
bash deploy/build-llama-server.sh  # 30–45 min on four cores; the site stays up
sudo cp deploy/aurora-llm.service deploy/aurora.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now aurora-llm
echo 'AURORA_LLM_URL=http://127.0.0.1:8081' | sudo tee -a .env
sudo systemctl restart aurora
```

The build script fetches ik_llama.cpp at the commit that was measured (the fork
publishes no releases) and installs one binary, built for this CPU, at
`/opt/aurora-llm/bin/llama-server`. It keeps the build under `/var/tmp`, so if
the install step fails, running it again only installs. The server reads the model path, threads
and context from the same `.env` as the app, so the two cannot disagree. Until
it has loaded, the page says the model is warming; past a minute without an
answer it says the server is not answering, and comes back by itself when it
does. The About page then says the model is held by `llama-server`, and every
memory figure counts both processes.

The server listens on loopback only, but any process on the machine can use
it. To have it answer only the app, give both the same key, the server's in a
file (on its command line it would show in `ps`):

```bash
cd /srv/aurora
openssl rand -hex 32 | sudo -u aurora tee llm.key >/dev/null && sudo chmod 600 llm.key
echo "AURORA_LLM_KEY=$(sudo cat llm.key)" | sudo tee -a .env >/dev/null
# and add  --api-key-file /srv/aurora/llm.key  to AURORA_LLM_ARGS in .env
sudo systemctl restart aurora-llm aurora
```

Back to the model in the app:

```bash
sudo sed -i '/^AURORA_LLM_URL=/d' .env
sudo systemctl disable --now aurora-llm && sudo systemctl restart aurora
```

#### Qwen3.5-4B

A 4B model chooses its tools far better than a 1B one, at about a third of
the speed. [Qwen3.5-4B](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF) is a
hybrid: 24 of its 32 layers are linear attention with a fixed state, and only
8 keep a KV cache, about 32 KiB a token, so a 16k window costs 0.5 GB. The
pinned ik_llama.cpp runs it as it is (dense Qwen3.5 support is from February
2026), text only.

It thinks before it answers. The app writes every prompt itself and sends it
to the server's `/completion` (`app/chatml.py`), so what is counted, what is
sent and what is replayed next turn are the same characters. An answer is kept
as the model wrote it, thinking and all: each message then begins with the
last, which on a model with recurrent layers is the difference between reading
the new message and reading the whole conversation again. `AURORA_THINKING`
says how it thinks (`on`, `off` for an empty block, `none` for a model without
one), and `AURORA_THINK_BUDGET` caps a reply's thinking: at the cap the reply
goes on with the line Qwen documents for ending a thought early.

```bash
cd /srv/aurora
sudo -u aurora curl -L -o models/Qwen3.5-4B-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf
sudo tee -a .env >/dev/null <<'EOF'
AURORA_MODEL_PATH=./models/Qwen3.5-4B-Q4_K_M.gguf
AURORA_MODEL_LABEL=Qwen3.5-4B
AURORA_MODEL_QUANT=Q4_K_M
AURORA_MODEL_PARAMS=4B
AURORA_N_CTX=16384
AURORA_KV_KIB_PER_TOKEN=32
AURORA_THINKING=on
AURORA_THINK_BUDGET=1024
EOF
sudo cp deploy/aurora-llm.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart aurora-llm aurora
```

The later lines of `.env` win, so the old model's lines can stay until the new
one has been tried. It needs about 3.7 GB, which is why `aurora-llm.service`
allows 4.5 GB. A reply goes on from where it stopped several times (out of its
thinking, into a file's content, out of it again), always after the server
stopped at a string it was given, so each request extends the last one
exactly. Whether the server reuses its cache across those requests is what
`scripts/eval_agent.py --engine real --probe-cache` shows: a request marked
`cont` should read only the tokens since the last one. `AURORA_LLM_ARGS` and
`deploy/templates/qwen3.5.jinja` are only for anyone who asks the server's
own chat endpoint; the app does not use it.

### The web search

The agent searches the web with no API key. Without any setup it reads
DuckDuckGo's HTML page, which works but is a page meant for a browser and can
refuse a server that asks too often. For a search of its own, install
[SearXNG](https://github.com/searxng/searxng) beside the app: it asks several
public engines at once and answers the app in JSON, on loopback only.

```bash
cd /srv/aurora
bash deploy/build-searxng.sh   # a few minutes; prints the commit to pin next time
echo 'AURORA_SEARX_URL=http://127.0.0.1:8888' | sudo tee -a .env
sudo systemctl restart aurora
```

It runs as its own user (`searxng`), in its own venv under `/opt/searxng`,
with its settings in `/etc/searxng/settings.yml` (a random secret is written on
install, and an edited file is never overwritten). The headlines come from
Google News's RSS feed and need nothing installed; `AURORA_NEWS_REGION` picks
the edition.

### The Python sandbox

The agent can run a short Python program to work something out exactly. It
runs under [bubblewrap](https://github.com/containers/bubblewrap), as the
app's own user: no network, nothing of the server's files but `/usr` and the
sandbox's own read-only venv, an empty `/work` and `/tmp` that go when it
ends, and kernel limits on memory (1 GiB), CPU time (10 s), file size and open
files, with a wall clock that kills the whole process group. It cannot make a
user namespace of its own (`--disable-userns`), a seccomp filter refuses the
kernel calls a program has no use for and an escape would need (`unshare`,
`mount`, `bpf`, `io_uring`, `ptrace`, `keyctl` and the like), it may start at
most 64 processes, and the kernel's out-of-memory killer takes it before the
app. What it prints is cut off at 64 KB. Where bubblewrap cannot run a
program, the tool is not offered at all; nothing ever runs outside it.

```bash
cd /srv/aurora
bash deploy/build-sandbox.sh   # bubblewrap, the seccomp filter, and numpy, pandas and sympy in /opt/aurora-sandbox/venv
sudo cp deploy/aurora.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl restart aurora
```

The script checks that bubblewrap is not setuid and that unprivileged user
namespaces are on, builds the venv owned by root, writes the filter to
`/opt/aurora-sandbox/seccomp.bpf`, and runs one program in the same jail as
the app's user, checking that it cannot make a namespace. Run it again after
a pull that changes it. Until the filter exists the app runs programs without
it and says so in the journal. It needs bubblewrap 0.8 or later; Debian 12
has 0.8.0. The unit's `TasksMax` bounds a fork bomb as well.

The model writes the program as its call's `code`, raw text in its call
format, so nothing has to be escaped, and the program is sampled as code. A
program you give yourself, `/python print(2**64)` or `/python` with a fenced
block, is run as you gave it.

Other deployment notes:

* `LimitMEMLOCK=infinity` in the unit is required if you set
  `AURORA_USE_MLOCK=true`. Without it llama.cpp can't pin the weights and quietly
  falls back to pageable memory. The model server always pins them (`--mlock`),
  and its unit carries the same line.
* `MemoryMax=3G` is an OOM guard, in both units — a service restarts instead of
  taking the whole VPS down with it.
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
sudo chown root:aurora /srv/aurora/.env      # the service reads it as aurora, and cannot change it
sudo chmod 640 /srv/aurora/.env
sudo systemctl restart aurora
sudo -u aurora .venv/bin/python -m app.gate status
```

`chmod 600` alone on a file root owns leaves the service unable to read its
own settings, and it does not start; the group read is what lets it.

The pattern never leaves the browser it was drawn in, and the setup page is
not part of any build. **The line is as good as the pattern**: there are few
enough patterns that whoever has it can try them all. It belongs in that one
file. To change the pattern, replace the line and restart; every session ends.

```bash
python -m app.gate status     # which way the gate is set, open sessions, guesses counted
python -m app.gate revoke     # end every session now
python -m app.gate reset      # forget the guesses counted so far
python -m app.gate enroll     # a one-time link that makes a new browser known
```

On the server these run as `sudo -u aurora .venv/bin/python -m app.gate …`.
`enroll` prints `/api/enroll/<code>`: open it once, within fifteen minutes, in
the browser the site should know. That browser then has guesses of its own,
apart from strangers', and still has to draw the pattern. It is the way in
from a new phone or a cleared browser on a day strangers have used up their
guesses.

### What it does and does not keep out

A session lasts until the browser closes, twelve hours at most, and an hour
if nothing is asked of the server. A browser that has opened the site before
is counted apart from strangers, so people playing with the sky cannot use up
its owner's tries; it stays known for 180 days from the last time it opened
the site. A wrong pattern, a throttled one and a malformed request get the
same answer after the same wait.

What this does not do:

* **Over plain HTTP it keeps out strangers, not eavesdroppers.** The pattern
  and the session cookie cross the network as they are. Anyone on the path
  can read either. Put TLS in front before trusting it with anything that
  matters; see below. Once the site is served over https,
  `AURORA_GATE_HTTPS_ONLY=true` refuses the pattern (and enrol links) over
  plain http unless they come from the server itself.
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
| `AURORA_LLM_URL` | *(unset)* | unset, the model is loaded into the app. An address (`http://127.0.0.1:8081`) sends every pass to the llama-server there; see [The model server](#the-model-server) |
| `AURORA_LLM_KEY` | *(unset)* | the key the model server asks every request for, when it is started with `--api-key-file`; see [The model server](#the-model-server) |
| `AURORA_N_CTX` | `16384` | the window: the tools' menu alone is about 2,000 tokens of it. ~0.5 GB of KV cache for Qwen3.5-4B, ~450 MB for Qwen2.5-1.5B |
| `AURORA_N_THREADS` | `0` | `0` is every CPU the service may use (affinity and cgroup quota, measured at startup). A number pins it. Measured on a 4-vCPU server: 8.9 tok/s at 2 threads, 17.1 at 4 |
| `AURORA_N_BATCH` | `256` | prompt-eval batch; 512 raises peak RAM for little gain here |
| `AURORA_USE_MLOCK` | `false` | `true` on the VPS, with the systemd unit above |
| `AURORA_CHAT_FORMAT` | `auto` | `auto` uses the GGUF's own template; `chatml` forces Qwen's |
| `AURORA_THINKING` | *(unset)* | how the model thinks before it answers: `on` (Qwen3.5 thinking), `off` (an empty `<think>` block, Qwen3.5 with its thinking off), `none` (no block). Unset: `off` where the older `AURORA_THINK_BLOCK=true` is set, else `none`. See [Qwen3.5-4B](#qwen35-4b) |
| `AURORA_THINK_BUDGET` | `1024` | with thinking on, the most tokens a reply thinks for before it is told to answer |
| `AURORA_LLM_ARGS` | *(unset)* | read only by `aurora-llm.service`: more `llama-server` options, word by word |
| `AURORA_HARDWARE_LABEL` | *(unset)* | how the machine is described in prose (About page, page description). Unset, it is written from what is measured, e.g. "machine with 2 cores and 3.8 GB of RAM". The status line always shows measured figures, whatever this says |
| `AURORA_MOCK` | `false` | run the site with no model present |
| `AURORA_MOCK_DELAY` | `0.085` | seconds between fake tokens; `0` runs the mock at full speed |
| `AURORA_TOOLS_ENABLED` | `true` | master switch for the tool belt |
| `AURORA_ALLOW_OUTBOUND` | `true` | `false` removes every network tool |
| `AURORA_TOOL_TIMEOUT` | `10` | seconds before a tool is abandoned |
| `AURORA_SEARCH` | `auto` | the web search: `auto` is SearXNG where `AURORA_SEARX_URL` is set and DuckDuckGo's HTML page where not; `searxng` or `duckduckgo` names one; `off` takes it off the menu |
| `AURORA_SEARX_URL` | *(unset)* | this server's own SearXNG, `http://127.0.0.1:8888`; see [The web search](#the-web-search) |
| `AURORA_NEWS_REGION` | `US:en` | the Google News edition the headlines are from, `COUNTRY:language` (`GB:en`, `DE:de`) |
| `AURORA_TIMEZONE` | `UTC` | the zone the model is told each message was sent in |
| `AURORA_SANDBOX` | `auto` | Python the model writes: `auto` runs it in the sandbox wherever the sandbox can run; `off` never. Nothing runs outside it; see [The Python sandbox](#the-python-sandbox) |
| `AURORA_SANDBOX_VENV` | `/opt/aurora-sandbox/venv` | the sandbox's own venv, read-only to it |
| `AURORA_KV_KIB_PER_TOKEN` | `28` | the model's KV cache per token, for the memory estimate on Benchmark: 28 for Qwen2.5-1.5B, 12 for LFM2.5-1.2B, 32 for Qwen3.5-4B |
| `AURORA_AGENT_LOOP` | `true` | let the model call a tool, read what came back and call another before it answers. Each call costs a reply, in seconds; `false` lets it call one. See *One call after another* |
| `AURORA_AGENT_MAX_STEPS` | `8` | with the loop on: tool calls for one message |
| `AURORA_AGENT_MAX_SECONDS` | `600` | with the loop on: seconds after which no further call is begun |
| `AURORA_NOTES` | `conversation` | notes the Agent keeps when asked to remember something. `conversation`: read back only in the conversation they were made in, and deleted with it. `site`: shared by every conversation, for a private deployment only. `off`: none. See *Notes* |
| `AURORA_STEPS_KEEP_DAYS` | `30` | days to keep the step-by-step record of each message (which tool, what it was given, what came back, tokens and time per pass). It is a copy of what visitors typed; `0` keeps none |
| `AURORA_USER_AGENT` | — | **must carry a contact URL** or Wikipedia 403s |
| `AURORA_GATE` | `on` | `off`, exactly, serves the site to everyone. Anything else keeps it behind the cover; see [The gate](#the-gate) |
| `AURORA_GATE_VERIFIER` | *(unset)* | the line the setup page prints for the pattern. Server's own `.env` only. Unset or malformed, nothing opens the site |
| `AURORA_GATE_SESSION_HOURS` | `12` | the longest a session lasts, however much it is used |
| `AURORA_GATE_IDLE_MINUTES` | `60` | how long a session lasts with nothing asked of the server |
| `AURORA_GATE_HTTPS_ONLY` | `false` | `true` refuses the pattern and enrol links over plain http unless they come from the server itself. Turn it on once the site has TLS |
| `AURORA_ALLOWED_HOSTS` | *(unset)* | with `AURORA_GATE=off`, the names the site answers to besides `localhost`, `127.0.0.1` and `::1`, comma-separated. Any other name gets `421`, so a page elsewhere cannot point its own name at a development server |
| `AURORA_CONFIRM_SECONDS` | `600` | how long the agent waits for you to allow a change it wants to make after reading the web, before it counts as no |

---

## Tools

Twenty tools, no API keys anywhere:

| Tool | | Network |
|---|---|---|
| `calculator` | exact arithmetic via `Fraction` — `0.1+0.2` really is `0.3` | — |
| `clock` | date and time; accepts `Europe/Paris`, `Tokyo`, `new york` or `Japan` | — |
| `convert_units` | length, mass, time, data, speed, volume, temperature | — |
| `text_stats` | words, sentences, reading time, top terms | — |
| `wikipedia` | article summaries | yes |
| `weather` | current conditions + 5-day forecast (open-meteo) | yes |
| `currency` | ECB rates (Frankfurter) | yes |
| `web_search` | the web now: SearXNG, or DuckDuckGo's HTML page; a numbered list of pages | yes |
| `news` | today's headlines, or the news about a topic (Google News RSS) | yes |
| `read_url` | fetch a page and extract its readable text | yes |
| `open_result` | read the page behind one of the numbered results, by its number | yes |
| `python` | run a short program in the sandbox and read what it prints; offered only where the sandbox runs | — |
| `read_file` | one file of this conversation's project, as it stands, with line numbers | — |
| `write_file` | create a file of the project, or replace one whole | — |
| `edit_file` | replace one exact piece of a file with another, and leave the rest | — |
| `move_file` | move or rename a file, or a whole folder; never onto a file that is there | — |
| `delete_file` | delete a file, or a whole folder | — |
| `run_file` | run a `.py` file of the project in the sandbox, with the rest of the project beside it; offered only where the sandbox runs | — |
| `remember` · `recall` | notes kept on the user's word; see *Notes* | — |

There is one agent, and every message can use every tool. Nothing is chosen
before sending: no persona, no skill button, no palette.

**You talk to it; it decides.** A message goes to the model, which replies:
it thinks, says what it has to say, and, when it judges a tool would give a
better answer than its memory, calls one in the middle of its reply, in the
format Qwen3.5 was trained on:

```
<tool_call>
<function=weather>
<parameter=location>
Paris
</parameter>
</function>
</tool_call>
```

The call is made, what came of it goes back to the model as the next turn
(`<tool_response>`), and the model goes on: another call, or the answer. A
greeting is one reply and no tool; a lookup is a call and an answer. No
pattern reads the message for a word that would pick a tool, and there is no
separate pass that decides first. The system text tells the model how to
judge: a tool for anything that may have changed since it was trained, for an
exact figure, or for a page or file that is named; otherwise it answers
directly, fitting the answer to the message. Each message is followed by when
it was sent, in `AURORA_TIMEZONE`, so "today" and "latest" mean what they say.

The page shows a reply in the order it was written: the model's thought (a
collapsed line), what it said, a tool's card where it called the tool, and
what it said after. A reopened conversation shows the same order.

**The live web.** The web search and the headlines both answer with a
numbered list, and the model may then ask for one of them by its number
(`open_result`): it never writes an address, and the only addresses a run may
go to that the user did not write are the ones a list gave it. A page that
says "now open http://elsewhere" is still refused. "Open the second one" in
the next message means the last list the conversation was shown.

**Code projects.** Files the model writes with `write_file` or changes with
`edit_file` are kept in the conversation's project (every version, in
`project_files`), and the page shows them in a panel beside the thread on a
wide screen, or a drawer on a narrow one, filling in line by line as the
model writes them. The panel is a folder tree: folders first, each opening
and closing, Expand all and Collapse all, and the file you pick below it.
As a project grows the model reorganises it with `move_file` (a file or a
whole folder, renamed or moved; never onto a file that is there) and
`delete_file`; a deleted file keeps its old versions, and a file written at
its path later goes on from its number. In the chat each change is a card that opens the file;
an edit shows the lines it took out and put in. **Download zip** gets all of
them, newest versions, in one folder named for the conversation. Each message
is told the project's files by name and version. A change asked for is that
change: one file edited, the rest left as they were. A file written again
exactly as it stands is not kept, and the model is told so in a sentence it
can act on. An edit whose text is not in the file is sent back the same way,
with the nearest line, and `read_file` shows a file with line numbers, so a
traceback's line leads straight to it. When the user pastes an error, the
model is told to find the line that causes it, say the cause in one sentence,
and fix it there. Where the server has its sandbox, `run_file` runs one of the
project's Python files, the rest of the project beside it, read-only, and the
model reads the end of what it printed on failure, where a traceback names
the error. A project holds at most 64 files, 256 KB each and 2 MB in all,
under plain relative names.

### How a call is kept honest

The model writes its own calls, so what keeps them usable is in the code that
reads them:

1. **Slash commands** — `/calc 1247*89`, `/weather Paris`, `/read <url>`,
   `/python <code>`. The user names the tool; the call is put into the
   conversation as if the model had made it, and the model answers from what
   came of it. Nothing on the page lists them.
2. **Read leniently, checked strictly.** A call is read out of the reply
   whatever is missing at its end, and its arguments are checked against the
   tool's: a number written as text becomes a number, an argument with a known
   set of values must be one of them (the model cannot invent a currency), a
   required one must be there. A call that names no tool there is, or cannot be
   read at all, or was already made in this message, is not made.
3. **Sent back, stated as fact.** Whatever was wrong goes back to the model as
   the call's result, in one sentence ("That call was not made: …"), and it
   goes on from there: it may call again, or answer.
4. **Failure is visible** — a tool that errors or times out renders as a failed
   card. The model is told too: for a tool that goes out to the web, what
   happened and not the advice a person is given beside it; for one of this
   machine's, the whole sentence, since it says what to do.

**Sampling follows what is being written.** A reply is written in phases, and
each change of phase is a stop the model server makes at a string it was
given, followed by a request that goes on from there: the thinking (to its
budget), the prose, and inside a call the value of a parameter that holds
code (a file's content, an edit's old and new text, a program), sampled as
Qwen recommends for code, with no presence penalty, so an edit can copy a
line exactly. Then prose again.

Under each answer, **How it ran** lists every step of the run in order: each
reply (what it read of its prompt, how much of it was already in the server's
cache, what it wrote and how long it took), each call, then the run's time
split into waiting, reading, writing and tools. It is read back from the steps
the answer kept, so a reopened conversation shows the same.

### One call after another

A reply that ends in a call is followed by another reply, which may call
again. `AURORA_AGENT_LOOP=false` turns that off: the model may call one tool,
then answers.

The model is not trusted to know when to stop. So the code that runs the loop
holds the limits:

- at most eight tool calls for one message, and no further call begun after
  ten minutes (both are settings);
- a call that has already been made is not made again;
- when a limit is reached, the last result says so in one line, and the last
  reply cannot call: it answers with what it has.

Two things keep the extra replies cheap. The whole run is one transcript that
only grows, and every reply reads it from the top, so each one after the first
is charged only for what is new. And the run takes the inference slot once and
keeps it, so nobody else's prompt replaces it in the model's cache part-way
through; if a tool goes out to the network while somebody is waiting, the slot
is lent to them for that time.

### What the next message reads

Each answered message keeps what the model was sent for it, from the message
to the answer: every reply as the model wrote it, thinking and calls
included, and what came of each call, as it was read. The next message is read with that, as it was. A follow-up
can then use an earlier result ("and is that warm for October?" after the
weather was looked up), and a model server that keeps its cache reads little
more than the new message, since the prompt begins with the last one and its
answer.

A result that goes out of date is replaced, once it has, by a line saying so:
the weather after three hours, a rate after a day, the time after ten minutes.
A conversation that outgrows the window loses its oldest turns in blocks, and
they are summed up in a few lines at the top. A message too long for the
window is cut, and the page says so. A conversation from before any of this
was kept is read as its messages.

What was sent is kept with the conversation and deleted with it. It holds
fetched pages, so it decides nothing: which addresses the reader may open is
decided by the visitor's own messages alone, and an address the model read in
a page is not one of them.

### Checking the answer

When a run has looked something up, its answer is held against what came
back before it is shown as finished. Every number in the answer is read with
its unit, and one that is in none of what was looked up (the results whole, as
the cards show them), nor in what the user wrote, is named in one line under
the answer: "Not in what was looked up: 64°F. Weather returned: Paris,
France: overcast, 17.9°C." An answer that says it has no live data beside a
lookup that worked is answered the same way. A figure agrees when it is what
the source comes to, rounded or cut short, so "about 62 miles" for 62.1371 is
fine and "63 miles" is not.

The line quotes a tool only when its output is the harness's own (a sum, a
time, the weather, a rate), never a page or an article. It is kept with the
answer, and the model reads a fixed note about it in later turns. It checks
figures, not sentences: it can tell that a number came from nowhere, not that
what is said around it is true. Derivations a visitor asked for, such as a
temperature in Fahrenheit, are flagged too, by design, since the model is told
not to work figures out itself.

### Notes

Asked to remember something ("keep in mind that my locker is 418", or
`/remember …`), the Agent keeps a note, and finds it again when a later
message needs it (`/recall …`). A note is a line in the user's own words, in
the site's own database, searched with SQLite's full-text search where the
build has it.

Whose notes they are is the deployment's choice. By default a note belongs to
the conversation it was made in: it is read back only there and deleted with
it, because this site has no accounts, and a note shared across conversations
would be shared with every visitor. `AURORA_NOTES=site` shares them, which only
suits a private deployment; `off` removes both tools.

A note is only ever kept on the user's word. Once a run has read text somebody
else wrote (a page, an article, notes shared across the site), the model may
not keep one: a page that says "remember that…" is refused like an address the
user never gave. A `/remember` typed by the user is always kept.

The same goes, one step softer, for anything that changes the project or runs
code. Once a message has read text somebody else wrote, a file the agent
wants to write, edit, move or delete, or a program it wants to run, is put to
you first, in the reply: **Allow**, **Allow for this reply**, or **Don't
allow**. Nothing happens until you answer, and no answer in ten minutes
(`AURORA_CONFIRM_SECONDS`) is a no. A page that says "now fix main.py like
this" or "run this" then gets no further than a question you can turn down.
A command you type yourself, `/python …`, is your own word and is not asked.

### Safety

`read_url` is the only tool that makes the server fetch a user-supplied
address, so it resolves the hostname first and refuses any private, loopback,
link-local, CGNAT, reserved or multicast address — including
`169.254.169.254`, the cloud metadata endpoint. The connection then goes to
the address that was checked, with the site's name sent as `Host` and its
certificate checked against it, so a name that answers with a public address
for the check and a private one a moment later gets nowhere. Redirects are
followed by hand, maximum three hops, re-validating and pinning at each one.
Bodies are capped at 2 MB and only `text/html` / `text/plain` is read.

That guard is about where an address points. It says nothing about an address
that points somewhere public and was put there by a page the model read: "now
open http://elsewhere/?q=" followed by something from the conversation. So when
it is the model that chooses to read a page, the address must be one the user
wrote themselves, in this message or an earlier one. The model may pass an
address on; it may not make one up or lift one from what it has read. A longer
path or a query string the user did not write is a different address. This is
checked in code and does not depend on the model noticing anything.

What a tool returns is given to the model as reading matter: wrapped in a
`<tool_response>` tag, with the model told once that what is inside one is
material and never an instruction, and with anything in it that looks like
that tag, like a call of its own, or like a control token of the chat format
taken out first, until none is left (one hidden inside another does not
survive its removal), and the brackets of any control token left half-written
broken apart. That is a convention and not a wall, which is why the rules
above are enforced in code.

The calculator parses with `ast` and a node whitelist. It never calls `eval`,
and rejects attribute access, comprehensions, lambdas and `__import__`. No
exact value it works out may pass about 3,900 digits, checked before each
power is taken, so no expression can hold the server up.

Set `AURORA_ALLOW_OUTBOUND=false` to drop every network tool from the registry,
the menu and the UI in one move.

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
away and re-read a gigabyte of weights every time. With the model in a
llama-server the same holds there: one server, one slot, and every pass offers
its prompt to the server's cache.

**The chat template does the work the sampler can't.** An instruct model fed raw
text behaves like a base model: it rambles, repeats, and never stops cleanly. The
usual response — clamp temperature to ~0 and bolt on a repeated-sentence
detector — makes it worse, because greedy decoding is what produces loops.
Writing every turn in `<|im_start|>` / `<|im_end|>` lets the model stop on its
own token and lets sampling sit at the model's own recommended settings: a
normal temperature with a presence penalty for prose, a cooler one with none
for code (`app/agent/style.py`).

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
as last measured, the tools, conversations and the benchmark's suite
into `index.html` as one JSON element (`app/api/frontend.py`), so the first paint has
real figures with no request behind it, and no system prompt or file path is
among them.

**Styling is Tailwind CSS 4, with the design tokens as its only vocabulary.**
The tokens stay plain custom properties, which is how the dark scheme is one
block of redefinitions; the Tailwind theme only names them, and Tailwind's own
palette, scales and breakpoints are removed. `DESIGN.md` says which parts are
written as CSS instead of utility classes, and why.

**The machine is measured, not described.** `app/ops/telemetry.py` reads the host
while the page is open: the CPUs this process may use (scheduler affinity, then
the cgroup's CPU quota, so `CPUQuota=150%` reads as 1.5 cores), load per core
from two readings of `/proc/stat`, memory in use and in total from
`/proc/meminfo`, what the app and its model hold resident (this process, plus
the llama-server found by its command line when the model runs in one), and the tightest
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
| `GET /api/conversations/{id}/project` | the project's files as they stand, with their contents; `…/project/files/{path}?version=` one of them; `…/project.zip` all of them |
| `POST /api/cancel/{id}` | stop a run, at any stage, by the `request_id` from its first event |
| `GET /api/health` | model, status, RSS, queue depth, uptime, and a `system` snapshot |
| `GET /api/telemetry` | the machine now: usable CPUs, load per core, memory used/total, the app and its model resident, cgroup limit, load average; plus engine `status`, `busy`, `queued` |
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
`app/ops/telemetry.py` against fixture trees of `/proc` and cgroup files (v2, v1,
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
stored. `test_calls.py` is the call format, `test_router.py` the slash
commands, `test_tools.py` argument checking, `test_projects.py` the files a
run writes and edits, and `test_steps.py` the stored trace. No test sleeps to let something happen,
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
thing happened: the right tool, the right arguments, no reply that was not
needed. `evals/tasks.jsonl` is a file of tasks, one per line, each with what to
send and what must be true afterwards. Tool results come from
`evals/fixtures`, so no task touches the network, and nothing is written to the
database.

```bash
python scripts/eval_agent.py                                   # scripted: no model needed
python scripts/eval_agent.py --engine real --out evals/reports/today.json
python scripts/eval_agent.py --engine real --compare evals/reports/before.json
python scripts/eval_agent.py --engine real --single            # one call where a task does not ask for more
python scripts/eval_agent.py --engine real --probe-cache       # what each request of four turns read
python scripts/eval_agent.py --engine real --rules evals/rules/fewer-calls.txt   # another wording, this run only
```

**Scripted**, each task brings what the model says, so the run measures the
code around the model and nothing about the model. It takes a second and runs
with the server's tests. Its report says its token counts are estimates and
carries no timings.

**Real** loads the model as the site does and gives every request the same seed.
It is the only source of a number about what the agent can do: task success,
routing accuracy, argument match, steps and replies, tokens, and seconds per
task split into waiting, reading and writing. Stop the web service first: a
second process loads a second copy of the model and shares the cores, exactly
as the benchmark would. With the model in a llama-server, stop `aurora` and
leave `aurora-llm` running: the run then uses that server, and two apps sharing
its one slot would each keep a queue the other cannot see.

The loop is on or off for a run of the suite as the settings say. A task that
is about the loop asks for it itself, so it runs with the loop on whatever the
settings say; `--single` lets all the others call once. Run the suite both
ways and compare: the report says how many runs made a second call, how many
were ended by a limit and how many calls repeated one already made, beside
task success and seconds per task. Scripted tasks are written for the loop on.

A wording file (`--rules`) puts other texts in front of the model for one
run: who the model is, the rules for using tools, and a tool's description.
It is how a rewording is measured before it is made the default, since with a
model this small a reworded line can move what it does either way. The report names
the file and its digest.

A task marked `pending` is a known gap, written down with its reason. It is
expected to fail; the day it passes, the run fails until the mark comes off.

---

## Limitations

It's a small model at 4 bits. On its own it gets arithmetic wrong, invents
citations, loses long reasoning chains, and knows nothing after its training;
the tools are there for the first and the last, and a search result is only
as good as the page it came from. It's good at short explanations,
quick drafts, summarising pasted text, and simple code — in a couple of seconds,
without a byte leaving the machine.
