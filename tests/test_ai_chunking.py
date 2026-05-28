import pytest

from app.ai.chunking import chunk_text


def test_chunk_text_empty_text_returns_no_chunks() -> None:
    assert chunk_text("   \n\t ") == []


def test_chunk_text_splits_text() -> None:
    chunks = chunk_text("a" * 2600, chunk_size=1000, overlap=200)

    assert len(chunks) == 3
    assert all(len(chunk) <= 1000 for chunk in chunks)


def test_chunk_text_overlap() -> None:
    text = "".join(str(index % 10) for index in range(1500))
    chunks = chunk_text(text, chunk_size=1000, overlap=200)

    assert chunks[0][-200:] == chunks[1][:200]


def test_chunk_text_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("text", chunk_size=100, overlap=100)
