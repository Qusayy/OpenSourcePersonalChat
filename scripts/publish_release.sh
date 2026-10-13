#!/usr/bin/env bash
# Publish the built interface to the `release` branch.
#
# `master` holds source only: web/dist is ignored there. A server must not need
# Node, so what it pulls is `release`, where every commit is one commit of
# `master` with its built files added.
#
#   scripts/publish_release.sh
#
# Run at the commit to publish (CI checks it out; see .github/workflows/ci.yml).
# It builds web/, then makes ONE commit:
#
#   tree     the checked-out commit's tree, plus web/dist
#   parent   the previous tip of `release` (none the first time)
#   message  "release: <short sha>" and the source commit's subject
#
# and pushes it without force. `release` is therefore a straight line that only
# ever grows: no merge, no rewrite, and `git pull` on the server always
# fast-forwards. Publishing the same tree twice does nothing.
#
# The commit is assembled with plumbing in a scratch index, so the working
# tree, the real index and the current branch are left exactly as they were.
#
# Environment:
#   RELEASE_REMOTE      where to push                      (default: origin)
#   RELEASE_BRANCH      the branch to publish to           (default: release)
#   RELEASE_SKIP_BUILD  1 = web/dist is already built; do not build again
#
# The commit is made as whoever git is configured to be (user.name and
# user.email, or GIT_AUTHOR_* / GIT_COMMITTER_*).

set -euo pipefail

REMOTE="${RELEASE_REMOTE:-origin}"
BRANCH="${RELEASE_BRANCH:-release}"
DIST="web/dist"

cd "$(git rev-parse --show-toplevel)"

source_sha="$(git rev-parse HEAD)"
short_sha="$(git rev-parse --short HEAD)"
subject="$(git log -1 --format=%s HEAD)"

if [ "${RELEASE_SKIP_BUILD:-}" != "1" ]; then
  echo "publish: building web/ at $short_sha"
  (cd web && npm ci && npm run build)
fi

if [ ! -f "$DIST/index.html" ]; then
  echo "publish: $DIST/index.html is missing; there is nothing to publish" >&2
  exit 1
fi

# The tree to publish: this commit's own tree with web/dist added. `add -f`
# because web/dist is ignored, which is the point of it not being on master.
scratch_index="$(mktemp)"
trap 'rm -f "$scratch_index"' EXIT
tree="$(
  export GIT_INDEX_FILE="$scratch_index"
  git read-tree HEAD
  git add -f -- "$DIST"
  git write-tree
)"

# `set -e` does not reach inside the $( ) above in every bash, so a failed
# `git add` there could still hand back a tree: the source without its built
# files, which is exactly what must never be published. Look, do not assume.
if [ -z "$(git ls-tree -r --name-only "$tree" -- "$DIST/index.html")" ]; then
  echo "publish: the tree to publish has no $DIST/index.html; refusing" >&2
  exit 1
fi

# The previous release, if there is one. Asked of the remote, not of whatever
# this clone happens to remember.
parent=""
if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
  # Only its tip is needed. A shallow clone (CI) takes just that; a full clone
  # is fetched normally, so that it is not turned into a shallow one.
  if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
    git fetch --quiet --depth=1 "$REMOTE" "$BRANCH"
  else
    git fetch --quiet "$REMOTE" "$BRANCH"
  fi
  parent="$(git rev-parse FETCH_HEAD)"
  if [ "$(git rev-parse "$parent^{tree}")" = "$tree" ]; then
    echo "publish: $BRANCH already holds this tree ($short_sha); nothing to do"
    exit 0
  fi
fi

message="release: $short_sha

$subject

Built from $source_sha."

if [ -n "$parent" ]; then
  commit="$(printf '%s\n' "$message" | git commit-tree "$tree" -p "$parent")"
else
  commit="$(printf '%s\n' "$message" | git commit-tree "$tree")"
fi

# No force. If someone else has published in the meantime this is refused,
# which is right: the next run publishes on top of theirs.
git push "$REMOTE" "$commit:refs/heads/$BRANCH"
echo "publish: $BRANCH is now $(git rev-parse --short "$commit") (from $short_sha)"
