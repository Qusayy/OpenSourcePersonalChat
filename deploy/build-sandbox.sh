#!/usr/bin/env bash
# Install what the agent's Python sandbox needs (app/sandbox/runner.py).
#
# A program the model wrote runs under bubblewrap: no network, nothing of the
# server's files but /usr and its own venv, a throwaway /tmp and /work, and
# limits on memory, CPU time, file size and processes. That needs bubblewrap,
# working unprivileged user namespaces (Debian 12 has them on), and a venv
# the sandbox may read and nobody may write.
#
# Run it from the checkout, as your own admin user (it needs sudo once):
#
#     bash deploy/build-sandbox.sh
#
# then restart aurora. The app finds the sandbox by itself: where bubblewrap
# cannot run a program, the python tool is not offered at all, and nothing is
# ever run outside it.
set -euo pipefail

PREFIX=${PREFIX:-/opt/aurora-sandbox}
HERE=$(cd "$(dirname "$0")" && pwd)

sudo apt-get install -y -q bubblewrap python3-venv util-linux

bwrap=$(command -v bwrap)
if [ -u "$bwrap" ]; then
  echo "$bwrap is setuid root. The sandbox runs it as aurora through user namespaces" >&2
  echo "and expects it not to be; see https://github.com/containers/bubblewrap" >&2
  exit 1
fi
if [ "$(sysctl -n kernel.unprivileged_userns_clone 2>/dev/null || echo 1)" != 1 ]; then
  echo "Unprivileged user namespaces are off. Turn them on with:" >&2
  echo "  echo kernel.unprivileged_userns_clone=1 | sudo tee /etc/sysctl.d/90-userns.conf && sudo sysctl --system" >&2
  exit 1
fi

# Owned by root and only readable by everyone else: a program in the sandbox
# can import from it and can change nothing in it.
sudo python3 -m venv "$PREFIX/venv"
sudo "$PREFIX/venv/bin/pip" install -q -U pip
sudo "$PREFIX/venv/bin/pip" install -q -r "$HERE/sandbox-requirements.txt"
sudo chown -R root:root "$PREFIX"
sudo chmod -R a+rX,go-w "$PREFIX"

# The same jail the app builds, run once as the app's own user.
sudo -u aurora "$bwrap" --unshare-all --die-with-parent --new-session \
  --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin \
  --ro-bind-try /etc/ld.so.cache /etc/ld.so.cache --ro-bind "$PREFIX" "$PREFIX" \
  --proc /proc --dev /dev --tmpfs /tmp --tmpfs /work --chdir /work --clearenv \
  -- "$PREFIX/venv/bin/python" -I -c \
  "import numpy, pandas, sympy, socket; print('sandbox ok: numpy', numpy.__version__)"
echo "Restart the app to offer the python tool: sudo systemctl restart aurora"
