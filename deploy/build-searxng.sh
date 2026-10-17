#!/usr/bin/env bash
# Install SearXNG, the web search Aurora's agent asks (deploy/searxng.service).
#
# Free and keyless: it asks several public engines at once and answers the app
# in JSON, on loopback. Without it the app falls back to DuckDuckGo's HTML
# page, which works with no setup at all but is a page meant for a browser.
#
# Run it from the checkout, as your own admin user (it needs sudo once):
#
#     bash deploy/build-searxng.sh                 # the newest SearXNG
#     REF=<commit> bash deploy/build-searxng.sh    # that commit, on purpose
#
# SearXNG publishes no releases. The commit installed is printed at the end:
# put it in REF the next time, so an upgrade is one you chose. Then, in the
# app's .env:
#
#     AURORA_SEARX_URL=http://127.0.0.1:8888
#
# and restart aurora. The search runs as its own user, searxng, with its own
# venv under /opt/searxng and its settings in /etc/searxng.
set -euo pipefail

REPO=https://github.com/searxng/searxng
REF=${REF:-master}
PREFIX=${PREFIX:-/opt/searxng}
HERE=$(cd "$(dirname "$0")" && pwd)

sudo apt-get install -y -q git python3-venv python3-dev build-essential \
  libxslt1-dev zlib1g-dev libffi-dev libssl-dev
id searxng >/dev/null 2>&1 || sudo useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin searxng
sudo install -d -o searxng -g searxng "$PREFIX"

src=$PREFIX/src
if [ ! -d "$src/.git" ]; then
  sudo -u searxng git clone -q "$REPO" "$src"
fi
sudo -u searxng git -C "$src" fetch -q origin "$REF"
sudo -u searxng git -C "$src" checkout -q FETCH_HEAD

sudo -u searxng python3 -m venv "$PREFIX/venv"
sudo -u searxng "$PREFIX/venv/bin/pip" install -q -U pip setuptools wheel pyyaml
sudo -u searxng "$PREFIX/venv/bin/pip" install -q -r "$src/requirements.txt"

# The settings are installed once, with a secret of their own; a later run
# leaves an edited file alone.
if [ ! -f /etc/searxng/settings.yml ]; then
  sudo install -d /etc/searxng
  secret=$(openssl rand -hex 32)
  sed "s/replaced-on-install/$secret/" "$HERE/searxng/settings.yml" |
    sudo tee /etc/searxng/settings.yml >/dev/null
  sudo chown root:searxng /etc/searxng/settings.yml
  sudo chmod 0640 /etc/searxng/settings.yml
fi

sudo install -m 0644 "$HERE/searxng.service" /etc/systemd/system/searxng.service
sudo systemctl daemon-reload
sudo systemctl enable --now searxng
sudo systemctl restart searxng

# It takes a few seconds to load its engines.
for _ in $(seq 1 20); do
  if curl -fsS 'http://127.0.0.1:8888/search?q=searxng&format=json' -o /dev/null; then
    echo "SearXNG answers on 127.0.0.1:8888 ($(git -C "$src" rev-parse --short=12 HEAD))"
    echo "Pin it: REF=$(git -C "$src" rev-parse HEAD)"
    exit 0
  fi
  sleep 1
done
echo "SearXNG did not answer on 127.0.0.1:8888; see: journalctl -u searxng -n 50" >&2
exit 1
