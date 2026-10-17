from collections.abc import Sequence

PROSE_SYSTEM = """You rewrite part of a novel for an alternate version of the story. You get the \
original text of one piece, the chapter's events as they now happen, the events that no longer \
happen, and facts that are now true.

Rules:
- Keep the author's voice, tense, point of view, names and wording wherever the events are \
unchanged. Change only what the new events and facts require.
- Every event listed under "Events now" must be depicted, in order. Events marked [NEW] are the \
ones to write in.
- Nothing under "No longer happens" may occur, be mentioned, or be reacted to (no mourning a death \
that did not happen, no consequences of a removed event).
- The facts hold throughout: a character recorded alive is alive and acts accordingly.
- Do not invent plot beyond the listed events. Small connective detail is fine.
- Keep roughly the original length. Output only the rewritten text, with no notes or headings."""


def prose_user(
    premise: str,
    facts: str,
    chapter: str,
    now: Sequence[str],
    removed: Sequence[str],
    original: str,
    previous_tail: str = "",
    feedback: Sequence[str] = (),
) -> str:
    parts = [
        f"Premise: {premise}",
        f"Facts in this version:\n{facts or '(none)'}",
        f"Chapter: {chapter}",
        "Events now (chapter-wide, in order):\n" + "\n".join(now or ["(none)"]),
        "No longer happens:\n" + "\n".join(removed or ["(nothing)"]),
    ]
    if previous_tail:
        parts.append(f"The rewritten text so far ends with:\n{previous_tail}")
    if feedback:
        problems = "\n".join(f"- {f}" for f in feedback)
        parts.append(f"Fix these problems found in the previous attempt:\n{problems}")
    parts.append(f"Original text of this piece:\n{original}\n\nRewrite it now.")
    return "\n\n".join(parts)


PROSE_CHECK_SYSTEM = """You check a rewritten chapter of an alternate version of a novel. You get \
the facts, the events that must happen (in order), the events that must not appear, and the \
rewritten text.

Report an issue only for a concrete failure:
- the text depicts, mentions, or reacts to an event listed under "No longer happens";
- an event under "Events now" is missing or contradicted;
- the text contradicts a fact (a character recorded alive is dead, absent or acting impossibly).

Do not report style or length. Quote the offending words in the problem. If the text is \
consistent, return no issues."""


def prose_check_user(facts: str, now: Sequence[str], removed: Sequence[str], text: str) -> str:
    return (
        f"Facts:\n{facts or '(none)'}\n\nEvents now:\n"
        + "\n".join(now or ["(none)"])
        + "\n\nNo longer happens:\n"
        + "\n".join(removed or ["(nothing)"])
        + f"\n\nRewritten text:\n{text}"
    )
