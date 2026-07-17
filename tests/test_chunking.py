from app.rag.chunking import StructureAwareChunker


def test_chunker_keeps_short_section() -> None:
    chunks = StructureAwareChunker(chunk_size=100, overlap=20).split("# 标题\n\n这是一个短段落。")
    assert chunks == ["# 标题\n\n这是一个短段落。"]


def test_chunker_splits_with_overlap() -> None:
    text = "第一句很长。" * 40
    chunks = StructureAwareChunker(chunk_size=80, overlap=10).split(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_chunker_never_emits_oversized_paragraph_after_short_text() -> None:
    text = "短段落。\n\n" + "长文本" * 100
    chunks = StructureAwareChunker(chunk_size=100, overlap=20).split(text)
    assert all(len(chunk) <= 100 for chunk in chunks)
