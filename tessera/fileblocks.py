"""Parse code files out of a model reply written as plain delimited blocks.

Format the Developer AI is asked to use (no JSON, so no string escaping to get wrong):

    === FILE: app/routers/notes.py ===
    <file content>
    === END FILE ===

A missing END marker is tolerated: the block then runs to the next FILE header or the end of the reply.
"""

from __future__ import annotations

import re

# Header is "=== FILE: path ===". Models sometimes drop the closing "===" or wrap it onto the next line.
_HEADER = re.compile(
    r"^=== FILE:[ \t]*(?P<path>[^\s=]+)[ \t]*(?:===)?[ \t]*(?:\n===[ \t]*)?$", re.MULTILINE
)
_END = re.compile(r"^=== END FILE ===[ \t]*$", re.MULTILINE)
_FENCE_LINE = re.compile(r"\A```[a-zA-Z0-9]*\n|\n```\s*\Z")


def parse_file_blocks(text: str) -> dict[str, str]:
    """Return {path: content}. Strips a markdown fence inside a block if the model added one."""
    files: dict[str, str] = {}
    headers = list(_HEADER.finditer(text))
    for i, h in enumerate(headers):
        limit = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        chunk = text[h.end() : limit]
        end = _END.search(chunk)
        if end:
            chunk = chunk[: end.start()]
        body = _FENCE_LINE.sub("", chunk.strip("\n")) + "\n"
        files[h.group("path")] = body
    return files