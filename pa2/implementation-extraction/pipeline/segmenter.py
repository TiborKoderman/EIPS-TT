"""Plain-text segmentation utilities (PA2)."""

from __future__ import annotations

from dataclasses import dataclass
import re


_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    char_count: int
    token_count: int


def normalize_text(text: str) -> str:
    text = (text or "").replace("\xa0", " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def build_short_char_chunks(
    cleaned_content: str,
    *,
    chunk_chars: int = 50,
    step_chars: int | None = None,
    min_chars: int = 1,
) -> list[TextChunk]:
    """Split text into fixed-size character chunks."""

    text = normalize_text(cleaned_content)
    if not text:
        return []

    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be > 0")

    step = chunk_chars if step_chars is None else step_chars
    if step <= 0:
        raise ValueError("step_chars must be > 0")

    chunks: list[TextChunk] = []
    idx = 0
    for start in range(0, len(text), step):
        piece = text[start:start + chunk_chars].strip()
        if len(piece) < min_chars:
            continue

        tokens = [token for token in piece.split(" ") if token]
        chunks.append(TextChunk(
            index=idx,
            text=piece,
            char_count=len(piece),
            token_count=len(tokens),
        ))
        idx += 1

    return chunks


def build_long_word_chunks(
    cleaned_content: str,
    *,
    words_per_chunk: int = 250,
    overlap_words: int = 50,
    min_words: int = 1,
) -> list[TextChunk]:
    """Split article-like text by paragraph/sentence boundaries."""

    if not cleaned_content or not cleaned_content.strip():
        return []

    paragraphs = [paragraph.strip() for paragraph in cleaned_content.split("\n\n") if paragraph.strip()]

    chunks: list[TextChunk] = []
    current_words: list[str] = []
    idx = 0

    def commit_chunk() -> None:
        nonlocal idx, current_words
        if len(current_words) < min_words:
            current_words = []
            return

        text = normalize_text(" ".join(current_words))
        chunks.append(TextChunk(
            index=idx,
            text=text,
            char_count=len(text),
            token_count=len(current_words),
        ))
        idx += 1

        if overlap_words > 0:
            current_words = current_words[-overlap_words:]
        else:
            current_words = []

    for paragraph in paragraphs:
        paragraph_words = paragraph.split()
        if len(current_words) + len(paragraph_words) <= words_per_chunk:
            current_words.extend(paragraph_words)
            continue

        if len(current_words) >= (words_per_chunk * 0.5):
            commit_chunk()

        if len(paragraph_words) > words_per_chunk:
            sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", paragraph) if sentence.strip()]
            for sentence in sentences:
                sentence_words = sentence.split()
                if len(current_words) + len(sentence_words) > words_per_chunk and current_words:
                    commit_chunk()
                current_words.extend(sentence_words)
            continue

        current_words.extend(paragraph_words)

    if current_words:
        commit_chunk()

    return chunks


def build_forum_long_chunks(
    post_blocks: list[str],
    *,
    words_per_chunk: int = 220,
    overlap_words: int = 30,
    min_words: int = 1,
) -> list[TextChunk]:
    """Chunk forum threads by post/reply first, then by paragraph/sentence."""

    normalized_blocks = [block.strip() for block in post_blocks if block and block.strip()]
    if not normalized_blocks:
        return []

    chunks: list[TextChunk] = []
    idx = 0
    for block in normalized_blocks:
        block_chunks = build_long_word_chunks(
            block,
            words_per_chunk=words_per_chunk,
            overlap_words=overlap_words,
            min_words=min_words,
        )
        for block_chunk in block_chunks:
            chunks.append(TextChunk(
                index=idx,
                text=block_chunk.text,
                char_count=block_chunk.char_count,
                token_count=block_chunk.token_count,
            ))
            idx += 1

    return chunks
