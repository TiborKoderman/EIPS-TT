"""Plain-text segmentation utilities (PA2).

This module implements TWO segmentation strategies required by the assignment:

1) Short segments: up to 50 characters
   - we do NOT respect sentence boundaries or whole words
   - chunks are created by fixed-size slicing

2) Long segments: 250 words
   - we DO respect whole words (split on whitespace)
   - chunk size measured in words

Both strategies are deterministic and easy to describe in the PA2 report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


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
	"""Split text into fixed-size char chunks (no word/sentence boundaries).

	- `chunk_chars`: max length of each chunk
	- `step_chars`: stride; defaults to `chunk_chars` (non-overlapping)
	"""

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
		piece = text[start : start + chunk_chars]
		piece = piece.strip()
		if len(piece) < min_chars:
			continue
		tokens = [t for t in piece.split(" ") if t]
		chunks.append(
			TextChunk(index=idx, text=piece, char_count=len(piece), token_count=len(tokens))
		)
		idx += 1

	return chunks


def build_long_word_chunks(
        cleaned_content: str,
        *,
        words_per_chunk: int = 250,
        overlap_words: int = 50,
        min_words: int = 1,
) -> list[TextChunk]:
        """Split text logically (Option B: Hybrid Paragraph/Sentence Chunking).

        - respect paragraphs (\n\n) as primary boundaries
        - bounds sizes with words_per_chunk
        - uses sentence splitting if a paragraph exceeds max words
        - supports overlap_words when splitting larger blocks
        """
        if not cleaned_content or not cleaned_content.strip():
                return []

        # 1. Split into natural paragraphs
        paragraphs = [p.strip() for p in cleaned_content.split("\n\n") if p.strip()]

        chunks: list[TextChunk] = []
        current_chunk_words: list[str] = []
        idx = 0

        def _commit_chunk():
                nonlocal idx, current_chunk_words
                if len(current_chunk_words) >= min_words:
                        text = normalize_text(" ".join(current_chunk_words))
                        chunks.append(TextChunk(
                                index=idx,
                                text=text,
                                char_count=len(text),
                                token_count=len(current_chunk_words)
                        ))
                        idx += 1

                        # Handle overlap for the next chunk
                        if overlap_words > 0:
                                current_chunk_words = current_chunk_words[-overlap_words:]
                        else:
                                current_chunk_words = []
                else:
                        current_chunk_words = []

        for p in paragraphs:
                p_words = p.split()

                # If adding this paragraph fits nicely:
                if len(current_chunk_words) + len(p_words) <= words_per_chunk:
                        current_chunk_words.extend(p_words)
                else:
                        # If we already have something substantial, commit it first
                        if len(current_chunk_words) >= (words_per_chunk * 0.5):
                                _commit_chunk()

                        # What if paragraph itself is HUGE? (Fallback to Sentence/Word slicing)
                        if len(p_words) > words_per_chunk:
                                sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', p) if s.strip()]
                                for s in sentences:
                                        s_words = s.split()
                                        if len(current_chunk_words) + len(s_words) > words_per_chunk and current_chunk_words:
                                                _commit_chunk()
                                        current_chunk_words.extend(s_words)
                        else:
                                current_chunk_words.extend(p_words)

        if current_chunk_words:
                _commit_chunk()

        return chunks




