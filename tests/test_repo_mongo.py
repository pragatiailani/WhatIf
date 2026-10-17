from app.repo import books, chunks, ensure_indexes, graph_levels, mentions
from app.repo.models import Book, Chunk, GraphLevel, GraphPayload, Mention, PageSpan


def test_ensure_indexes(mongo):
    ensure_indexes()
    assert "book_id_1_seq_1" in mongo["chunks"].index_information()


def test_books_roundtrip(mongo):
    book = books.create_book(Book(title="Kim", content_hash="abc"))
    assert books.get_book(book.id) == book
    assert books.find_by_content_hash("abc") == book

    books.set_status(book.id, "failed", error="boom")
    books.update_stats(book.id, {"chunks": 3})
    books.update_stats(book.id, {"entities": 7})
    stored = books.get_book(book.id)
    assert (stored.status, stored.error) == ("failed", "boom")
    assert stored.stats == {"chunks": 3, "entities": 7}
    assert books.get_book("missing") is None


def test_chunks_order_and_range(mongo):
    spans = [PageSpan(page=1, char_start=0, char_end=4)]
    items = [Chunk(book_id="b", seq=s, text=f"t{s}", page_spans=spans) for s in (2, 0, 1)]
    chunks.insert_chunks(items)

    assert [c.seq for c in chunks.list_chunks("b")] == [0, 1, 2]
    assert [c.seq for c in chunks.list_chunks("b", seq_from=1, seq_to=1)] == [1]
    ids = [items[1].id, "missing", items[0].id]
    assert [c.seq for c in chunks.get_chunks(ids)] == [0, 2]
    assert chunks.get_chunk(items[0].id).page_spans == spans
    assert chunks.count_chunks("b") == 3
    assert chunks.delete_chunks("b") == 3


def test_mentions_order_and_rekey(mongo):
    def m(key, seq, start):
        return Mention(
            book_id="b", node_key=key, chunk_id=f"c{seq}", chunk_seq=seq,
            quote="q", char_start=start, char_end=start + 1, page=1,
        )

    mentions.insert_mentions([m("ravi", 1, 5), m("ravi", 0, 9), m("r", 0, 1)])
    assert [(x.chunk_seq, x.char_start) for x in mentions.list_for_node("b", "ravi")] == [
        (0, 9),
        (1, 5),
    ]
    assert mentions.rekey("b", ["r"], "ravi") == 1
    assert len(mentions.list_for_node("b", "ravi")) == 3
    assert [x.char_start for x in mentions.list_for_chunk("c0")] == [1, 9]


def test_graph_levels_upsert_and_resume(mongo):
    def lvl(level, index, n):
        return GraphLevel(
            book_id="b", level=level, index=index, seq_start=index, seq_end=index,
            graph=GraphPayload(entities=[{"key": str(i)} for i in range(n)]),
        )

    first = graph_levels.save_level_graph(lvl(0, 0, 1))
    again = graph_levels.save_level_graph(lvl(0, 0, 2))  # resumed run rewrites the slot
    assert again.id == first.id and len(again.graph.entities) == 2

    graph_levels.save_level_graph(lvl(0, 1, 1))
    graph_levels.save_level_graph(lvl(1, 0, 1))
    assert [g.index for g in graph_levels.list_level("b", 0)] == [0, 1]
    assert graph_levels.count_level("b", 0) == 2
    assert graph_levels.max_level("b") == 1
    assert graph_levels.max_level("other") is None
    assert graph_levels.delete_levels("b", from_level=1) == 1
