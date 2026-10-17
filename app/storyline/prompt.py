from collections.abc import Sequence

JUDGE_SYSTEM = """You check an alternate version of a novel for continuity. The story was changed at \
one point (the premise below). Everything after it is the original story, kept, hidden or rewritten.

You get the premise, the facts now true of some characters, and one chapter's events in story \
order. Events marked [NEW] were written for this version; the rest are from the original book.

Report an issue only for a concrete contradiction, for example:
- a character acts, speaks or is present after being recorded dead or missing;
- an event reacts to something the new version no longer contains (mourning a death that did not \
happen, a trial for a crime that was removed);
- a character knows or does something their situation in this version rules out;
- two events in the chapter cannot both be true.

Do not report style, pacing, or things that are merely different from the original. If the chapter \
is consistent, return no issues. Give the key of the event at fault, and say how it should change."""


def judge_user(premise: str, facts: str, chapter: str, events: Sequence[str]) -> str:
    return (
        f"Premise: {premise}\n\nFacts in this version:\n{facts or '(none)'}\n\n"
        f"Chapter: {chapter}\nEvents in order:\n" + "\n".join(events)
    )
