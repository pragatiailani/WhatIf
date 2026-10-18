"""Helpers for assembling an agent's system prompt from sections."""

SEPARATOR = "\n\n" + "=" * 80 + "\n\n"


def join_sections(*sections: str) -> str:
    """The sections in order, each trimmed and separated by a clear divider."""
    return SEPARATOR.join(s.strip() for s in sections if s and s.strip())
