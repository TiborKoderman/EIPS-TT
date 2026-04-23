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
	overlap_words: int = 0,
	min_words: int = 1,
) -> list[TextChunk]:
	"""Split text into word-based chunks (keeps whole words).

	- `words_per_chunk`: chunk size in words
	- `overlap_words`: optional overlap to preserve context
	"""

	text = normalize_text(cleaned_content)
	if not text:
		return []

	if words_per_chunk <= 0:
		raise ValueError("words_per_chunk must be > 0")
	if overlap_words < 0:
		raise ValueError("overlap_words must be >= 0")
	if overlap_words >= words_per_chunk:
		raise ValueError("overlap_words must be < words_per_chunk")

	words = [w for w in text.split(" ") if w]
	if not words:
		return []

	step = words_per_chunk - overlap_words
	chunks: list[TextChunk] = []
	idx = 0
	for start in range(0, len(words), step):
		window = words[start : start + words_per_chunk]
		if len(window) < min_words:
			continue
		piece = " ".join(window).strip()
		if not piece:
			continue
		chunks.append(
			TextChunk(
				index=idx,
				text=piece,
				char_count=len(piece),
				token_count=len(window),
			)
		)
		idx += 1

		if start + words_per_chunk >= len(words):
			break

	return chunks




