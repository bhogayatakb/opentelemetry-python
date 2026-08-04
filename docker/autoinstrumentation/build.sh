#!/usr/bin/env bash
# Builds the custom autoinstrumentation-python image from a version-aligned
# checkout of this fork, not from whatever commit happens to be checked out.
#
# Why this script exists instead of a plain `docker build .`: this repo's
# main branch runs ahead of the contrib release train pinned in
# requirements.txt (e.g. main is 1.45.0.dev0/0.66b0.dev while
# opentelemetry-instrumentation==0.64b0 hard-pins
# opentelemetry-semantic-conventions==0.64b0 exactly). Building directly from
# main makes pip's resolver fail with ResolutionImpossible -- or, if you work
# around that by installing api/sdk/semconv as a separate pip invocation
# layered on top, produces a WORSE failure: `pip install --target` treats the
# whole `opentelemetry/` namespace directory as one opaque unit for its
# "already exists, skip" check, so whichever invocation runs second silently
# drops every *other* package's `opentelemetry/*` files -- including
# opentelemetry/instrumentation/sitecustomize.py, the actual bootstrap
# entrypoint the whole PYTHONPATH auto-instrumentation mechanism depends on.
# That produces an image that copies in fine but instruments and exports
# nothing, with no error anywhere.
#
# The fix: build from a worktree checked out at the upstream tag matching
# requirements.txt's pins, with just this fork's small diff (the two new
# _mw_*.py files, the record_exception hook, and the entry-points line)
# copied on top -- then a single `pip install` in the Dockerfile can resolve
# everything together in one pass, same as an unmodified upstream build.
#
# Usage: docker/autoinstrumentation/build.sh [image-tag]
# Bump UPSTREAM_TAG below in lockstep whenever requirements.txt's
# opentelemetry-instrumentation/opentelemetry-distro pin changes.

set -euo pipefail

UPSTREAM_TAG="v1.43.0"
IMAGE_TAG="${1:-mw-autoinstrumentation-python:local}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKTREE_DIR="$(mktemp -d /tmp/mw-otel-autoinstrumentation-build.XXXXXX)"
trap 'git -C "$REPO_ROOT" worktree remove --force "$WORKTREE_DIR" 2>/dev/null || rm -rf "$WORKTREE_DIR"' EXIT

echo "==> Fetching $UPSTREAM_TAG from upstream open-telemetry/opentelemetry-python"
git -C "$REPO_ROOT" fetch https://github.com/open-telemetry/opentelemetry-python.git "tag" "$UPSTREAM_TAG" --no-tags

echo "==> Checking out a worktree at $UPSTREAM_TAG"
git -C "$REPO_ROOT" worktree add --detach "$WORKTREE_DIR" "$UPSTREAM_TAG"

echo "==> Applying this fork's diff on top"
cp "$REPO_ROOT/opentelemetry-sdk/src/opentelemetry/sdk/trace/_mw_exception_context.py" \
   "$WORKTREE_DIR/opentelemetry-sdk/src/opentelemetry/sdk/trace/_mw_exception_context.py"
cp "$REPO_ROOT/opentelemetry-sdk/src/opentelemetry/sdk/resources/_mw_vcs.py" \
   "$WORKTREE_DIR/opentelemetry-sdk/src/opentelemetry/sdk/resources/_mw_vcs.py"

python3 - "$WORKTREE_DIR/opentelemetry-sdk/src/opentelemetry/sdk/trace/__init__.py" <<'PYEOF'
import sys
path = sys.argv[1]
text = open(path).read()

import_anchor = "from opentelemetry.sdk.trace._tracer_metrics import create_tracer_metrics"
import_patch = (
    "from opentelemetry.sdk.trace._mw_exception_context import (  # mw: fork-only exception source capture\n"
    "    get_exception_source_attributes,\n"
    ")\n"
) + import_anchor
if "_mw_exception_context" not in text:
    assert import_anchor in text, "import anchor not found -- upstream tag structure changed"
    text = text.replace(import_anchor, import_patch, 1)

hook_anchor = "            EXCEPTION_ESCAPED: str(escaped),\n        }\n        if attributes:"
hook_patch = (
    "            EXCEPTION_ESCAPED: str(escaped),\n"
    "        }\n"
    "        # mw: fork-only addition, opt-in via MW_RECORD_EXCEPTION_SOURCE\n"
    "        _attributes.update(get_exception_source_attributes(exception))\n"
    "        if attributes:"
)
if "get_exception_source_attributes(exception)" not in text:
    assert hook_anchor in text, "record_exception anchor not found -- upstream tag structure changed"
    text = text.replace(hook_anchor, hook_patch, 1)

open(path, "w").write(text)
PYEOF

python3 - "$WORKTREE_DIR/opentelemetry-sdk/pyproject.toml" <<'PYEOF'
import sys
path = sys.argv[1]
text = open(path).read()
anchor = 'service_instance = "opentelemetry.sdk.resources:ServiceInstanceIdResourceDetector"'
patch = anchor + (
    '\n# mw: fork-only, opt-in via OTEL_EXPERIMENTAL_RESOURCE_DETECTORS=vcs\n'
    'vcs = "opentelemetry.sdk.resources._mw_vcs:VcsResourceDetector"'
)
if "_mw_vcs:VcsResourceDetector" not in text:
    assert anchor in text, "entry-points anchor not found -- upstream tag structure changed"
    text = text.replace(anchor, patch, 1)
open(path, "w").write(text)
PYEOF

mkdir -p "$WORKTREE_DIR/docker/autoinstrumentation"
cp "$REPO_ROOT/docker/autoinstrumentation/Dockerfile" "$WORKTREE_DIR/docker/autoinstrumentation/Dockerfile"
cp "$REPO_ROOT/docker/autoinstrumentation/requirements.txt" "$WORKTREE_DIR/docker/autoinstrumentation/requirements.txt"

echo "==> Building $IMAGE_TAG"
docker buildx build --platform linux/amd64,linux/arm64 -f "$WORKTREE_DIR/docker/autoinstrumentation/Dockerfile" -t "$IMAGE_TAG" "$WORKTREE_DIR"

echo "==> Done: $IMAGE_TAG"
