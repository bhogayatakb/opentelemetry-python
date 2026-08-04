# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Middleware fork-only addition.

Captures the source code of every frame in an exception's traceback, so it
can be attached to the "exception" span event alongside the standard
``exception.*`` attributes. This mirrors the exception-context work done in
middleware-labs/agent-apm-python (PRs #56, #57, #59), reimplemented at the
single choke point every instrumentation library shares:
``opentelemetry.sdk.trace.Span.record_exception``.

Kept in its own module, separate from ``Span.record_exception``, to keep the
upstream diff this depends on as small as possible and to make it easy to
carry forward across rebases onto new upstream releases.

Disabled by default. Enable with the ``MW_RECORD_EXCEPTION_SOURCE``
environment variable (accepts "true"/"false", case-insensitive).
"""

from __future__ import annotations

import inspect
import json
import traceback
from logging import getLogger
from os import environ
from types import FrameType, TracebackType
from typing import Mapping

from opentelemetry.util import types

_logger = getLogger(__name__)

_ENV_VAR_ENABLED = "MW_RECORD_EXCEPTION_SOURCE"
_ENV_VAR_MAX_CHARS = "MW_RECORD_EXCEPTION_SOURCE_MAX_CHARS"
_DEFAULT_MAX_CHARS = 8192

# Above this many lines, a frame's function body is windowed down to
# _CONTEXT_LINES above/below the line that raised, instead of dumping the
# whole function.
_MAX_FUNCTION_LINES = 20
_CONTEXT_LINES = 10

EXCEPTION_LANGUAGE = "exception.language"
EXCEPTION_STACK_DETAILS = "exception.stack_details"


def _is_enabled() -> bool:
    return environ.get(_ENV_VAR_ENABLED, "false").strip().lower() == "true"


def _max_chars() -> int:
    raw = environ.get(_ENV_VAR_MAX_CHARS)
    if raw is None:
        return _DEFAULT_MAX_CHARS
    try:
        return int(raw)
    except ValueError:
        _logger.warning(
            "Invalid value for %s: %r. Falling back to default of %d.",
            _ENV_VAR_MAX_CHARS,
            raw,
            _DEFAULT_MAX_CHARS,
        )
        return _DEFAULT_MAX_CHARS


def _extract_function_body(frame: FrameType, lineno: int) -> dict:
    """Best-effort source extraction for a single frame, windowed down to
    _CONTEXT_LINES around `lineno` when the function is longer than
    _MAX_FUNCTION_LINES."""
    try:
        source_lines, start_line = inspect.getsourcelines(frame)
    except (OSError, TypeError) as error:
        return {
            "function_code": f"Could not retrieve source code: {error}",
            "start_line": None,
            "end_line": None,
        }

    end_line = start_line + len(source_lines) - 1
    if len(source_lines) > _MAX_FUNCTION_LINES:
        start_idx = max(0, lineno - start_line - _CONTEXT_LINES)
        end_idx = min(len(source_lines), lineno - start_line + _CONTEXT_LINES)
        source_lines = source_lines[start_idx:end_idx]
        start_line += start_idx
        end_line = start_line + len(source_lines) - 1

    return {
        "function_code": "".join(source_lines),
        "start_line": start_line,
        "end_line": end_line,
    }


def _build_stack_details(tb: TracebackType) -> list:
    """One entry per traceback frame, deepest (where the exception was
    raised) first -- matching the order the UI expects a root cause in."""
    stack_details = []
    for frame, lineno in traceback.walk_tb(tb):
        code = frame.f_code
        function_details = _extract_function_body(frame, lineno)
        stack_details.insert(
            0,
            {
                "exception.file": code.co_filename,
                "exception.line": lineno,
                "exception.function_name": code.co_name,
                "exception.function_body": function_details["function_code"],
                "exception.start_line": function_details["start_line"],
                "exception.end_line": function_details["end_line"],
                "exception.is_file_external": (
                    "true" if "site-packages" in code.co_filename else "false"
                ),
            },
        )
    return stack_details


def get_exception_source_attributes(
    exception: BaseException,
) -> Mapping[str, types.AttributeValue]:
    """Best-effort extraction of source code for every frame in the
    exception's traceback.

    Returns an empty mapping if the feature is disabled via env var or the
    exception has no traceback.
    """
    if not _is_enabled():
        return {}

    tb = exception.__traceback__
    if tb is None:
        return {}

    stack_details = _build_stack_details(tb)
    if not stack_details:
        return {}

    stack_details_json = json.dumps(stack_details)
    max_chars = _max_chars()
    if len(stack_details_json) > max_chars:
        stack_details_json = stack_details_json[:max_chars] + "... (truncated)"

    return {
        EXCEPTION_LANGUAGE: "python",
        EXCEPTION_STACK_DETAILS: stack_details_json,
    }
