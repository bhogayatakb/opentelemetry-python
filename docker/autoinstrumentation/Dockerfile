# Custom drop-in replacement for
# ghcr.io/open-telemetry/opentelemetry-operator/autoinstrumentation-python,
# bundling this fork's opentelemetry-api/sdk (exception source capture + VCS
# resource detector) instead of the public PyPI releases.
#
# Do NOT build this Dockerfile directly against whatever commit is checked
# out on this branch -- use docker/autoinstrumentation/build.sh instead:
#   docker/autoinstrumentation/build.sh <registry>/autoinstrumentation-python:<tag>
# main runs ahead of the contrib release train pinned below (e.g. main may be
# 1.45.0.dev0 while opentelemetry-instrumentation==0.64b0 hard-pins
# opentelemetry-semantic-conventions==0.64b0 exactly), which makes the single
# `pip install` below fail dependency resolution outright. build.sh builds
# from a worktree at the matching upstream tag with just this fork's small
# diff applied on top, so the versions here always resolve cleanly. See its
# comments for the full story, including a worse failure mode this avoids.
#
# The output layout under /autoinstrumentation matches what the OpenTelemetry
# Operator's init container expects (a flat `pip install --target` dump), so
# this image can be referenced as-is from an Instrumentation CR's
# spec.python.image field -- no operator changes needed.
#
# This image is just a file source copied into the target app pod by the
# Operator's init container -- nothing in it actually runs, so setting env
# vars here would have no effect on the instrumented app. To turn the new
# features on, set these on the *target application* pod instead (e.g. via
# the Instrumentation CR's spec.env, or the pod spec directly):
#   MW_RECORD_EXCEPTION_SOURCE=true
#   OTEL_EXPERIMENTAL_RESOURCE_DETECTORS=vcs
# The vcs detector falls back to reading the local .git checkout via the
# `git` CLI, but most production app images have neither `git` nor a `.git`
# directory -- for those, also set MW_VCS_REPOSITORY_URL / MW_VCS_COMMIT_SHA
# explicitly (e.g. injected from CI at build/deploy time).

FROM python:3.12-slim AS build

WORKDIR /operator-build

# Install this fork's opentelemetry-api / opentelemetry-sdk /
# opentelemetry-semantic-conventions together with the pinned contrib
# instrumentation set, opentelemetry-distro, and opentelemetry-instrumentation
# in ONE pip invocation.
#
# This MUST be a single `pip install` call, not split across two (as earlier
# versions of this Dockerfile did, in either order). `opentelemetry.*` is a
# PEP 420 implicit namespace package shared by all of these distributions.
# `pip install --target DIR` treats the top-level `opentelemetry/` directory
# as one opaque unit for its "already exists" check -- not per sub-package --
# so whichever separate `pip install --target workspace ...` invocation runs
# *second* has its `opentelemetry/*` content silently dropped for every
# package in that command (pip still prints "Successfully installed" for
# them; only the shared-namespace files are skipped). That includes
# opentelemetry/instrumentation/, which contains sitecustomize.py -- the
# actual bootstrap entrypoint the whole PYTHONPATH auto-instrumentation
# mechanism depends on -- so a split install produces an image whose init
# container copies files in successfully but never actually instruments or
# exports anything. Passing every requirement to a single pip invocation
# lets pip's resolver merge them all into `opentelemetry/` in one pass, the
# same way the unmodified upstream Dockerfile's single install step always
# has. Since the local paths are direct requirements (not transitive), pip
# uses them to satisfy every other package's opentelemetry-api/sdk/
# semantic-conventions dependency instead of fetching a PyPI version.
COPY opentelemetry-api ./src/opentelemetry-api
COPY opentelemetry-sdk ./src/opentelemetry-sdk
COPY opentelemetry-semantic-conventions ./src/opentelemetry-semantic-conventions
COPY docker/autoinstrumentation/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --target workspace \
    ./src/opentelemetry-api \
    ./src/opentelemetry-sdk \
    ./src/opentelemetry-semantic-conventions \
    opentelemetry-distro==0.64b0 \
    opentelemetry-instrumentation==0.64b0 \
    -r requirements.txt

FROM busybox AS autoinstrumentation-image
COPY --from=build /operator-build/workspace /autoinstrumentation
RUN chmod -R go+r /autoinstrumentation
