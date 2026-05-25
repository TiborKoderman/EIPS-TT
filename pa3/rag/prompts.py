"""Prompt construction for the two PA3 answering modes."""

from __future__ import annotations

from typing import Any, Iterable


RAG_SYSTEM_PROMPT = """Odgovarjaš v slovenščini kot previden informacijski pomočnik.
Odgovor utemelji izključno na podanih virih. Ne dodajaj dejstev, ki jih v virih ni.
Vsako vsebinsko trditev podpri z navedbo vira v obliki [1] ali [2].
Če viri ne zadoščajo za odgovor, to jasno povej in ne ugibaj.
Pri zdravstvenih temah ne postavljaj diagnoze in uporabniku svetuj strokovno pomoč,
če gre za nujno ali osebno zdravstveno vprašanje."""


LLM_ONLY_SYSTEM_PROMPT = """Odgovarjaš v slovenščini kot previden informacijski pomočnik.
Odgovori jedrnato. Nimaš priloženih zunanjih dokumentov, zato ne navajaj virov,
URL-jev ali navideznih citatov. Pri zdravstvenih temah ne postavljaj diagnoze
in omeni, da splosen odgovor ne nadomesti zdravstvene obravnave."""


def build_with_context_messages(query: str, hits: Iterable[Any]) -> list[dict[str, str]]:
    """Construct Ollama chat messages containing explicit retrieved evidence."""

    evidence_blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        url = _field(hit, "url", "")
        text = _field(hit, "segment_text", "")
        evidence_blocks.append(f"[{index}] Vir: {url}\n{text}")

    evidence = "\n\n".join(evidence_blocks) if evidence_blocks else "(Ni pridobljenih virov.)"
    user_prompt = (
        f"Vprašanje: {query}\n\n"
        "Pridobljeni dokazi:\n"
        f"{evidence}\n\n"
        "Odgovori na vprašanje samo na podlagi pridobljenih dokazov. "
        "Na koncu dodaj kratko vrstico 'Uporabljeni viri:' z oznakami virov, "
        "ki si jih dejansko uporabil."
    )
    return [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_without_context_messages(query: str) -> list[dict[str, str]]:
    """Construct Ollama chat messages without retrieved evidence."""

    return [
        {"role": "system", "content": LLM_ONLY_SYSTEM_PROMPT},
        {"role": "user", "content": f"Vprašanje: {query}\n\nOdgovori kratko in jasno."},
    ]


def _field(item: Any, key: str, default: str) -> str:
    if isinstance(item, dict):
        return str(item.get(key, default) or default)
    return str(getattr(item, key, default) or default)
