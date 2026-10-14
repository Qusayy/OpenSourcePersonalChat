#!/usr/bin/env bash
# Build the llama-server that holds Aurora's model (deploy/aurora-llm.service).
#
# It is ik_llama.cpp's server, at the commit that was measured on the 4-core
# server this site runs on: with the same Qwen file it wrote 23-24 tok/s
# against 15.6 in the app's own process, and it keeps a conversation's prompt
# cached for models llama-cpp-python has to re-read on every turn. The fork
# publishes no releases, so the commit is pinned here and moved on purpose.
#
# Built for this machine's CPU (GGML_NATIVE) into one binary with no shared
# libraries beside it, and installed where the unit expects it:
#
#     deploy/build-llama-server.sh                    # /opt/aurora-llm/bin/llama-server
#     JOBS=4 deploy/build-llama-server.sh             # more compile jobs, more memory
#     PREFIX=/somewhere/else deploy/build-llama-server.sh
#
# The site can stay up: the compile runs at low priority, and two jobs keep it
# inside the memory the site leaves free. It takes 30-45 minutes on four
# cores. Needs git, cmake and a C++ compiler; installing asks for sudo once.
set -euo pipefail

COMMIT=3dbc2c9f899519c5daaa83ea683dc99b146fddae
REPO=https://github.com/ikawrakow/ik_llama.cpp
PREFIX=${PREFIX:-/opt/aurora-llm}
JOBS=${JOBS:-2}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

git -C "$work" init -q
git -C "$work" remote add origin "$REPO"
git -C "$work" fetch -q --depth 1 origin "$COMMIT"
git -C "$work" checkout -q FETCH_HEAD

cmake -S "$work" -B "$work/build" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF
nice -n 10 cmake --build "$work/build" --config Release -j "$JOBS" --target llama-server

sudo install -D -m 0755 "$work/build/bin/llama-server" "$PREFIX/bin/llama-server"
echo "Installed $PREFIX/bin/llama-server (ik_llama.cpp ${COMMIT:0:7})"
