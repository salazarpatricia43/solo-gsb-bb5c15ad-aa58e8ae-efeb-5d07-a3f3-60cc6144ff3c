"""Route matching that keeps opaque ids intact inside path parameters.

``POST /events`` accepts any non-empty ``event_id`` / ``participant_id``:
ids may contain ``/`` or ``%``, may be non-ASCII, and may even be spelled
exactly like a literal route (``changes``, ``compare``, ...). Starlette's
default routing matches against the *decoded* path, which breaks such ids
twice over:

* ``GET /events/a%2Fb`` was decoded to ``/events/a/b`` before matching, so
  the single-segment ``{event_id}`` pattern no longer matched (404);
* ``GET /events/changes`` — even percent-encoded as ``/events/%63hanges`` —
  decoded to the literal static route and was claimed by it.

:class:`OpaqueIdRoute` instead matches the **raw** (still percent-encoded)
request path and percent-decodes the captured parameters afterwards:

* ``%2F`` is not a literal ``/`` in the raw path, so ``{event_id}`` spans
  it and decodes to ``a/b`` — one opaque segment, exactly as stored;
* ``/events/%63hanges`` does not match the literal ``/events/changes``
  route and falls through to ``/events/{event_id}`` with id ``changes``;
* literal requests keep matching their literal routes, so the existing
  static routes (``/events/changes``, ``/participants/compare``, ...)
  behave exactly as before.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope


class OpaqueIdRoute(APIRoute):
    """APIRoute matching the raw path, decoding path params after the match."""

    def matches(self, scope: Scope) -> tuple[Match, dict[str, Any]]:
        if scope["type"] != "http" or scope.get("raw_path") is None:
            return super().matches(scope)

        # Percent-encoded octets are pure ASCII; a lenient UTF-8 decode
        # additionally keeps any raw non-ASCII bytes a client sent usable.
        route_path = scope["raw_path"].decode("utf-8", errors="surrogateescape")
        root_path = scope.get("root_path", "")
        if root_path and route_path.startswith(root_path):
            route_path = route_path[len(root_path):]

        match = self.path_regex.match(route_path)
        if match is None:
            return Match.NONE, {}

        matched_params = {
            key: self.param_convertors[key].convert(unquote(value))
            for key, value in match.groupdict().items()
        }
        path_params = dict(scope.get("path_params", {}))
        path_params.update(matched_params)
        child_scope = {
            "endpoint": self.endpoint,
            "path_params": path_params,
            "route": self,
        }
        if self.methods and scope["method"] not in self.methods:
            return Match.PARTIAL, child_scope
        return Match.FULL, child_scope
