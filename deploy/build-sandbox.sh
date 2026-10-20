#!/usr/bin/env bash
# Install what the agent's Python sandbox needs (app/sandbox/runner.py).
#
# A program the model wrote runs under bubblewrap: no network, nothing of the
# server's files but /usr and its own venv, a throwaway /tmp and /work, and
# limits on memory, CPU time, file size and processes. It may make no
# namespace of its own, and a seccomp filter written here refuses it the
# system calls an escape starts with. That needs bubblewrap 0.8 or later
# (Debian 12 has 0.8.0), working unprivileged user namespaces (Debian 12 has
# them on), a venv the sandbox may read and nobody may write, and
# python3-seccomp to write the filter with.
#
# Run it from the checkout, as your own admin user (it needs sudo once):
#
#     bash deploy/build-sandbox.sh
#
# then restart aurora. Run it again when this file changes: the filter is
# written here and nowhere else. The app finds the sandbox by itself: where
# bubblewrap cannot run a program, the python tool is not offered at all, and
# nothing is ever run outside it.
set -euo pipefail

PREFIX=${PREFIX:-/opt/aurora-sandbox}
HERE=$(cd "$(dirname "$0")" && pwd)

sudo apt-get install -y -q bubblewrap python3-venv util-linux python3-seccomp

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

# The seccomp filter, beside the venv, where the app looks for it. It is
# replaced whole, so a program never starts with half of one.
sudo /usr/bin/python3 -I - "$PREFIX/seccomp.bpf" <<'PY'
import errno
import os
import sys

import seccomp

# Calls a program run for a visitor has no use for, and an escape from the
# jail starts with: new namespaces, mounts, keyrings, kernel programs and
# modules, io_uring, ptrace. They fail as if not permitted.
REFUSED = (
    "unshare", "setns", "mount", "umount2", "pivot_root",
    "keyctl", "add_key", "request_key",
    "bpf", "perf_event_open", "userfaultfd",
    "io_uring_setup", "io_uring_enter", "io_uring_register",
    "ptrace", "kexec_load", "kexec_file_load",
    "init_module", "finit_module", "delete_module",
    "swapon", "swapoff",
)
# The newer calls that mount, where this libseccomp knows them.
NEWER = ("open_tree", "move_mount", "fsopen", "fsconfig", "fsmount", "fspick", "mount_setattr")
# clone makes namespaces too, with these flags in its first argument (x86-64
# and arm64; s390x has the arguments the other way round). clone3 hides its
# flags where a filter cannot read them, so it fails as if the kernel had
# none, and the C library falls back to clone.
CLONE_NEW = {
    "NEWNS": 0x00020000, "NEWCGROUP": 0x02000000, "NEWUTS": 0x04000000, "NEWIPC": 0x08000000,
    "NEWUSER": 0x10000000, "NEWPID": 0x20000000, "NEWNET": 0x40000000,
}

target = sys.argv[1]
f = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
for name in REFUSED:
    f.add_rule(seccomp.ERRNO(errno.EPERM), name)
for name in NEWER:
    try:
        f.add_rule(seccomp.ERRNO(errno.EPERM), name)
    except (RuntimeError, ValueError):
        print(f"seccomp: this libseccomp does not know {name}; it is left to the kernel")
for flag in CLONE_NEW.values():
    f.add_rule(seccomp.ERRNO(errno.EPERM), "clone", seccomp.Arg(0, seccomp.MASKED_EQ, flag, flag))
f.add_rule(seccomp.ERRNO(errno.ENOSYS), "clone3")

partial = target + ".new"
with open(partial, "wb") as out:
    f.export_bpf(out)
os.chmod(partial, 0o644)
os.replace(partial, target)
print("seccomp filter written to", target)
PY

sudo chown -R root:root "$PREFIX"
sudo chmod -R a+rX,go-w "$PREFIX"
sudo chmod 0644 "$PREFIX/seccomp.bpf"

# The same jail the app builds, run as the app's own user, with the filter
# handed over the way the app hands it: open on a descriptor bubblewrap reads.
jail() {
  sudo -u aurora -- /bin/bash -c 'exec 3<"$1" && shift && exec "$@"' jail "$PREFIX/seccomp.bpf" \
    "$bwrap" --unshare-all --unshare-user --disable-userns --die-with-parent --new-session \
    --cap-drop ALL --seccomp 3 \
    --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin \
    --ro-bind-try /etc/ld.so.cache /etc/ld.so.cache --ro-bind "$PREFIX" "$PREFIX" \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /work --chdir /work --clearenv \
    -- "$@"
}

jail "$PREFIX/venv/bin/python" -I -c \
  "import numpy, pandas, sympy, socket; print('sandbox ok: numpy', numpy.__version__)"
if said=$(jail /usr/bin/unshare -U /usr/bin/true 2>&1); then
  echo "A program in the sandbox made a user namespace; it must not be able to." >&2
  exit 1
fi
echo "sandbox ok: it cannot make a user namespace (${said:-refused})"
echo "Restart the app to offer the python tool: sudo systemctl restart aurora"
