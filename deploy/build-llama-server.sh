#!/usr/bin/env bash
# Build the llama-server that holds Aurora's model (deploy/aurora-llm.service).
#
# It is ik_llama.cpp's server, at the commit that was measured on the 4-core
# server this site runs on: with the same Qwen file it wrote 23-24 tok/s
# against 15.6 in the app's own process, and it keeps a conversation's prompt
# cached for models llama-cpp-python has to re-read on every turn. The fork
# publishes no releases, so the commit is pinned here and moved on purpose.
#
# Run it as your own admin user, not as aurora: the service user has no sudo,
# and installing into /opt needs it once. The service only has to be able to
# read and run the result.
#
#     bash deploy/build-llama-server.sh               # /opt/aurora-llm/bin/llama-server
#     JOBS=4 bash deploy/build-llama-server.sh        # more compile jobs, more memory
#     PREFIX=/somewhere/else bash deploy/build-llama-server.sh
#
# Built for this machine's CPU (GGML_NATIVE) into one binary with no shared
# libraries beside it. The site can stay up: the compile runs at low priority,
# and two jobs keep it inside the memory the site leaves free. The first build
# takes 30-45 minutes on four cores. The build is kept in BUILD (by default
# /var/tmp/aurora-llm-build-<user>), so if anything after it fails, running
# this again picks up where it stopped instead of compiling everything twice.
set -euo pipefail

COMMIT=3dbc2c9f899519c5daaa83ea683dc99b146fddae
REPO=https://github.com/ikawrakow/ik_llama.cpp
PREFIX=${PREFIX:-/opt/aurora-llm}
JOBS=${JOBS:-2}
BUILD=${BUILD:-/var/tmp/aurora-llm-build-$(id -un)}

src=$BUILD/src
if [ "$(git -C "$src" rev-parse HEAD 2>/dev/null || true)" != "$COMMIT" ]; then
  rm -rf "$src"
  mkdir -p "$src"
  git -C "$src" init -q
  git -C "$src" remote add origin "$REPO"
  git -C "$src" fetch -q --depth 1 origin "$COMMIT"
  git -C "$src" checkout -q FETCH_HEAD
fi

cmake -S "$src" -B "$BUILD/build" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF
nice -n 10 cmake --build "$BUILD/build" --config Release -j "$JOBS" --target llama-server

built=$BUILD/build/bin/llama-server
target=$PREFIX/bin/llama-server
if mkdir -p "$PREFIX/bin" 2>/dev/null && [ -w "$PREFIX/bin" ]; then
  install -m 0755 "$built" "$target"
elif ! sudo install -D -m 0755 "$built" "$target"; then
  echo "Built $built but could not install it. As a user who can sudo, run:" >&2
  echo "  sudo install -D -m 0755 $built $target" >&2
  exit 1
fi
echo "Installed $target (ik_llama.cpp ${COMMIT:0:7})"
