# Aurora

A web front-end that makes a tiny local model look intentional.

`Qwen2.5-1.5B-Instruct` at `Q4_K_M`, served from a 2 vCPU / 4 GB VPS with no GPU,
no API key and no upstream provider. The performance story isn't hidden — it's
the feature: live tokens/sec while the answer streams, time-to-first-token under
every reply, and a benchmark page that measures the machine it's running on and
plots it against reference hardware.

```
app/
  main.py        FastAPI: pages, SSE chat, benchmark, history
  llm.py         one Llama instance, one lock, honest metrics
  bench.py       the benchmark suite + reference data
  personas.py    four presets (system prompt + sampling profile)
  db.py          SQLite (WAL): conversations, messages, runs
  templates/     Jinja2 — chat, bench, about
  static/        hand-written CSS + ES modules, no build step
scripts/bench_cli.py    headless benchmark → JSON
deploy/                 systemd unit + nginx site
```

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

### 3. Run

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
benchmark, so you can develop the front-end anywhere. On Windows:

```powershell
$env:AURORA_MOCK="true"; .venv\Scripts\python -m uvicorn app.main:app --port 8000
```

---

## Deploying

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

Other deployment notes:

* `LimitMEMLOCK=infinity` in the unit is required if you set
  `AURORA_USE_MLOCK=true`. Without it llama.cpp can't pin the weights and quietly
  falls back to pageable memory.
* `MemoryMax=3G` is an OOM guard — the service restarts instead of taking the
  whole VPS down with it.
* Keep ~2 GB of swap as a safety net. In normal operation the process should
  never touch it.
* `pip install psutil` if you want the memory readouts on non-Linux hosts; on
  Linux they come from `/proc/self/status` for free.

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
| `AURORA_HARDWARE_LABEL` | `2 vCPU · 4 GB VPS` | shown throughout the UI |
| `AURORA_MOCK` | `false` | run the site with no model present |

---

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

**The front-end has no dependencies.** No build step, no CDN, no third-party
JavaScript — the markdown renderer is ~150 lines in `static/js/md.js`, and it
escapes everything before producing a tag. The aurora background is soft radial
gradients animated with `transform` only (no `filter: blur()`, no per-frame
repaint), and message cards use a translucent fill rather than `backdrop-filter`,
which is what keeps a long thread scrolling smoothly on a weak client.

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
| `POST /api/chat` → SSE | events: `conversation`, `queue`, `start`, `token`, `done`, `error` |
| `POST /api/cancel/{id}` | stop an in-flight generation |
| `GET /api/health` | model, status, RSS, queue depth, uptime |
| `GET/DELETE /api/conversations[/{id}]` | history |
| `POST /api/bench/run` → SSE | `stage`, `rep`, `case`, `summary` |
| `GET /api/bench/results` | latest summary + history + reference data |

```bash
curl -N -X POST localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"count to twenty"}'
```

Tokens must appear one at a time. If they arrive all at once, something between
you and uvicorn is buffering — see the nginx note above.

---

## Limitations

It's a 1.5B model at 4 bits. It gets arithmetic wrong, invents citations, loses
long reasoning chains, and knows nothing recent. It's good at short explanations,
quick drafts, summarising pasted text, and simple code — in a couple of seconds,
without a byte leaving the machine.
