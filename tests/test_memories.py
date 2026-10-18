from app.memory.service import ExtractionResult, MemoryCandidate, extract_and_update_memory
from app.repo import memories
from app.repo.models import UserMemory


def candidate(**kwargs):
    values = {
        "user_id": "u1", "type": "preference", "key": "genre_comedy",
        "value": "likes comedy books", "scope_type": "global", "source": "inferred",
        "confidence": 0.82, "status": "pending", "last_session_id": "s1",
    }
    values.update(kwargs)
    return UserMemory(**values)


def test_inferred_memory_requires_repeated_evidence(mongo):
    first = memories.apply_memory_delta(candidate(), "add")
    assert first.status == "pending"
    assert memories.relevant_memories("u1", "book1") == []

    second = memories.apply_memory_delta(candidate(last_session_id="s2"), "reinforce")
    assert second.status == "active"
    assert second.evidence_count == 2
    assert memories.relevant_memories("u1", "book1")[0].key == "genre_comedy"


def test_explicit_preference_overrides_inferred_but_inference_cannot_override_explicit(mongo):
    memories.apply_memory_delta(candidate(), "add")
    explicit = candidate(value="dislikes comedy", source="explicit", confidence=1.0,
                         status="active", last_session_id="s2")
    saved = memories.apply_memory_delta(explicit, "replace")
    assert saved.status == "active" and saved.source == "explicit"
    inferred = candidate(value="likes comedy books", last_session_id="s3")
    after = memories.apply_memory_delta(inferred, "replace")
    assert after.value == "dislikes comedy"
    assert after.source == "explicit"


def test_memory_scope_isolated_by_book_and_storyline(mongo):
    global_pref = candidate(source="explicit", status="active")
    book_pref = candidate(key="book_interest", scope_type="book", book_id="book1",
                          source="explicit", status="active")
    story_pref = candidate(key="story_choice", scope_type="storyline", book_id="book1",
                           storyline_id="alt1", source="explicit", status="active")
    memories.apply_memory_delta(global_pref)
    memories.apply_memory_delta(book_pref)
    memories.apply_memory_delta(story_pref)
    keys = {m.key for m in memories.relevant_memories("u1", "book1", "alt1")}
    assert keys == {"genre_comedy", "book_interest", "story_choice"}
    keys_other = {m.key for m in memories.relevant_memories("u1", "book2", None)}
    assert keys_other == {"genre_comedy"}


def test_forget_deletes_active_and_pending(mongo):
    memories.apply_memory_delta(candidate(), "add")
    assert memories.forget_by_key("u1", "genre_comedy", "global", None, None)
    assert memories.relevant_memories("u1", "book1") == []


class FakeStructuredLLM:
    def __init__(self, result):
        self.result = result
        self.schema = None
        self.prompt = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        return self

    async def ainvoke(self, prompt):
        self.prompt = prompt
        return self.result


async def test_llm_returns_separate_structured_memory_delta(mongo):
    llm = FakeStructuredLLM(ExtractionResult(memories=[MemoryCandidate(
        type="preference", key="answer_style_length", value="concise", operation="add",
        scope_type="global", confidence=1.0, source="explicit", evidence="user explicitly asked",
    )]))
    applied = await extract_and_update_memory(
        llm, user_id="u1", session_id="s1", book_id="book1", storyline_id=None,
        question="Please keep answers concise", answer="Sure, I will keep it concise.",
    )
    assert llm.schema is ExtractionResult
    assert applied == [{"key": "answer_style_length", "value": "concise", "operation": "add",
                        "status": "active", "scope_type": "global", "source": "explicit"}]
    assert "memory delta" in llm.prompt
    assert memories.relevant_memories("u1", "book1")[0].value == "concise"
