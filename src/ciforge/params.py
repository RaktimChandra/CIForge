"""TeamCity-style parameter references: ``%name%`` in scripts, env values and images.

Resolution order (later wins): pipeline ``params`` → build ``params`` → run parameters
(``--param`` / API) → built-ins. ``%env.NAME%`` reads the server's environment. ``%%`` is a
literal percent sign.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

BUILTINS = {
    "ciforge.run.id",
    "ciforge.build.id",
    "ciforge.build.name",
    "ciforge.workspace",
    "ciforge.artifacts",
    "vcs.commit",
    "vcs.branch",
}

_REF = re.compile(r"%%|%([A-Za-z_][A-Za-z0-9_.\-]*)%")


class ParamError(KeyError):
    pass


def substitute(text: str, params: Mapping[str, str], environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name is None:
            return "%"
        if name.startswith("env."):
            value = env.get(name[4:])
            if value is None:
                raise ParamError(f"environment variable {name[4:]} referenced as %{name}% is not set")
            return value
        if name not in params:
            raise ParamError(f"unknown parameter %{name}%")
        return params[name]

    return _REF.sub(repl, text)


def mask(text: str, secrets: list[str]) -> str:
    """Replaces secret values (at least 4 characters long) with ***."""
    for s in sorted(secrets, key=len, reverse=True):
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return text
