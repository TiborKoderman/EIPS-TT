"""Article-only ekstrakcija in čiščenje za MedOverNet (PA2).

Cilji dizajna:
- ekstrakcija naj bo deterministična in razložljiva v poročilu,
- izogibanje krhkim pravilom z enim samim selektorjem,
- rezultat naj vsebuje revizijske podatke (is_article + reason),
- izhod naj bo kakovosten plain text za segmentacijo/embeddinge.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag


_BLOCK_TAGS_TO_DROP: tuple[str, ...] = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "form",
    "button",
    "input",
    "select",
    "option",
    "textarea",
    "header",
    "footer",
    "nav",
    "aside",
)

_NOISE_KEYWORDS: tuple[str, ...] = (
    "header",
    "footer",
    "menu",
    "nav",
    "cookie",
    "share",
    "social",
    "related",
    "teaser",
    "recommend",
    "promo",
    "widget",
    "sidebar",
    "banner",
    "login",
    "register",
    "forum",
    "portali",
    "newsletter",
    "breadcrumbs",
    "comment",
)

_ARTICLE_CONTAINER_SELECTORS: tuple[str, ...] = (
    "article",
    "main article",
    "main [itemprop='articleBody']",
    "[itemprop='articleBody']",
    ".article-content",
    ".article__content",
    ".entry-content",
    ".post-content",
    ".content-article",
    ".single-content",
    "main",
)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ArticleExtractionResult:
    """Strukturiran rezultat ekstrakcije članka."""

    is_article: bool
    reason: str
    url: str
    title: str | None
    author: str | None
    published_at: str | None
    section_headings: list[str]
    body_paragraphs: list[str]
    cleaned_content: str


def extract_medover_article(url: str, html: str) -> ArticleExtractionResult:
    """Iz HTML strani izlušči besedilo in metapodatke članka.

    Zakaj tak pipeline:
    1) URL filter hitro izloči očitne ne-article strani (manj false positive primerov).
    2) Izbira content-root uporablja več selektorjev + scoring (robustno med template variacijami).
    3) Boilerplate + link-density cleanup odstrani statične/ponavljajoče bloke.
    4) Končni pragovi velikosti preprečijo, da bi šum/noise obravnavali kot članek.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    if not _is_article_candidate_url(url):
        return ArticleExtractionResult(
            is_article=False,
            reason="URL does not match article scope (forum/listing/system page).",
            url=url,
            title=None,
            author=None,
            published_at=None,
            section_headings=[],
            body_paragraphs=[],
            cleaned_content="",
        )

    title = _extract_title(soup)
    author, published_at = _extract_author_and_date(soup)
    root = _select_best_article_root(soup)
    if root is None:
        return ArticleExtractionResult(
            is_article=False,
            reason="No stable article content container found.",
            url=url,
            title=title,
            author=author,
            published_at=published_at,
            section_headings=[],
            body_paragraphs=[],
            cleaned_content="",
        )

    working = BeautifulSoup(str(root), "html.parser")
    _remove_boilerplate(working)
    _remove_link_heavy_blocks(working)

    section_headings = _extract_section_headings(working)
    body_paragraphs = _extract_body_paragraphs(working)
    cleaned_content = _compose_cleaned_text(
        title=title,
        section_headings=section_headings,
        body_paragraphs=body_paragraphs,
    )

    if len(body_paragraphs) < 3 or len(cleaned_content) < 500:
        return ArticleExtractionResult(
            is_article=False,
            reason="Insufficient article body after cleanup.",
            url=url,
            title=title,
            author=author,
            published_at=published_at,
            section_headings=section_headings,
            body_paragraphs=body_paragraphs,
            cleaned_content=cleaned_content,
        )

    return ArticleExtractionResult(
        is_article=True,
        reason="Article extracted successfully.",
        url=url,
        title=title,
        author=author,
        published_at=published_at,
        section_headings=section_headings,
        body_paragraphs=body_paragraphs,
        cleaned_content=cleaned_content,
    )


def _is_article_candidate_url(url: str) -> bool:
    """Hiter URL filter za izločitev znanih ne-article poti.

    Filter je namerno širok; končno odločitev sprejme content-based del.
    """
    parsed = urlparse(url)
    path = (parsed.path or "").lower().strip("/")
    if not path:
        return False
    blocked_segments = (
        "forum/",
        "forum",
        "kontakt",
        "prijava",
        "registracija",
        "kategorija",
        "iskanje",
        "tag/",
        "author/",
    )
    if any(segment in path for segment in blocked_segments):
        return False
    return True


def _extract_title(soup: BeautifulSoup) -> str | None:
    """Izlušči naslov po prioriteti: H1 -> og:title -> document title."""
    h1 = soup.find("h1")
    if h1:
        text = _clean_text(h1.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        text = _clean_text(str(og.get("content")))
        if text:
            return text
    if soup.title:
        title_text = _clean_text(soup.title.get_text(" ", strip=True))
        if title_text:
            return title_text.split(" - ")[0].strip()
    return None


def _extract_author_and_date(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Izlušči avtorja/datum najprej iz meta tagov, nato JSON-LD fallback.

    Meta tagi so običajno bolj čisti; JSON-LD pokrije strani, kjer meta manjka.
    """
    author = None
    published = None

    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content"):
        author = _clean_text(str(meta_author.get("content")))

    for prop in ("article:published_time", "og:published_time"):
        meta_date = soup.find("meta", attrs={"property": prop})
        if meta_date and meta_date.get("content"):
            published = _clean_text(str(meta_date.get("content")))
            break

    json_ld_author, json_ld_date = _extract_jsonld_author_date(soup)
    if not author and json_ld_author:
        author = json_ld_author
    if not published and json_ld_date:
        published = json_ld_date

    return author, published


def _extract_jsonld_author_date(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Prebere avtorja/datum iz strukturiranih podatkov, če so na voljo."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        for doc in _parse_jsonld_documents(raw):
            article_node = _find_article_node(doc)
            if article_node is None:
                continue
            author = _extract_author_from_node(article_node)
            published = _extract_date_from_node(article_node)
            if author or published:
                return author, published
    return None, None


def _parse_jsonld_documents(raw: str) -> list[object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _find_article_node(node: object) -> dict[str, object] | None:
    if isinstance(node, dict):
        type_value = node.get("@type")
        if _is_article_type(type_value):
            return node
        graph = node.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                found = _find_article_node(item)
                if found is not None:
                    return found
    elif isinstance(node, list):
        for item in node:
            found = _find_article_node(item)
            if found is not None:
                return found
    return None


def _is_article_type(type_value: object) -> bool:
    if isinstance(type_value, str):
        return type_value.lower() in {"article", "newsarticle", "blogposting"}
    if isinstance(type_value, list):
        return any(isinstance(item, str) and item.lower() in {"article", "newsarticle", "blogposting"} for item in type_value)
    return False


def _extract_author_from_node(node: dict[str, object]) -> str | None:
    author = node.get("author")
    if isinstance(author, str):
        return _clean_text(author)
    if isinstance(author, dict):
        name = author.get("name")
        if isinstance(name, str):
            return _clean_text(name)
    if isinstance(author, list):
        names: list[str] = []
        for item in author:
            if isinstance(item, str):
                names.append(_clean_text(item))
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(_clean_text(str(item.get("name"))))
        joined = ", ".join(name for name in names if name)
        return joined or None
    return None


def _extract_date_from_node(node: dict[str, object]) -> str | None:
    for key in ("datePublished", "dateModified"):
        value = node.get(key)
        if isinstance(value, str):
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned
    return None


def _select_best_article_root(soup: BeautifulSoup) -> Tag | None:
    """Izbere najbolj verjeten vsebinski container članka.

    Zakaj scoring (namesto enega selektorja):
    - strani imajo različne template,
    - score nagradi daljše besedilo in več odstavkov,
    - score kaznuje link-heavy node, ki je pogosto teaser/navigacija.
    """
    candidates: list[Tag] = []
    for selector in _ARTICLE_CONTAINER_SELECTORS:
        candidates.extend(soup.select(selector))

    unique_candidates = _dedupe_tags(candidates)
    if not unique_candidates:
        return None

    scored: list[tuple[int, Tag]] = []
    for candidate in unique_candidates:
        text = _clean_text(candidate.get_text(" ", strip=True))
        if len(text) < 400:
            continue
        paragraph_count = len(candidate.find_all("p"))
        heading_count = len(candidate.find_all(["h2", "h3"]))
        link_count = len(candidate.find_all("a"))
        # Utežena heuristika: odstavki so najmočnejši signal dejanske vsebine.
        score = len(text) + (paragraph_count * 120) + (heading_count * 40) - (link_count * 10)
        scored.append((score, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _remove_boilerplate(soup: BeautifulSoup) -> None:
    """Odstrani statične ne-vsebinske bloke po tag tipu in class/id markerjih."""
    for tag_name in _BLOCK_TAGS_TO_DROP:
        for node in soup.find_all(tag_name):
            node.decompose()

    # Iteriramo čez snapshot list, ker se node-i med decompose spreminjajo.
    for node in list(soup.find_all(True)):
        if not isinstance(node, Tag):
            continue
        if node.attrs is None:
            continue
        marker = _node_marker(node)
        if any(keyword in marker for keyword in _NOISE_KEYWORDS):
            node.decompose()


def _remove_link_heavy_blocks(soup: BeautifulSoup) -> None:
    """Odstrani bloke, kjer prevladuje anchor text (menuji/related/teaserji)."""
    for node in list(soup.find_all(["div", "section", "ul", "ol"])):
        total_text = _clean_text(node.get_text(" ", strip=True))
        if len(total_text) < 120:
            continue
        links_text = _clean_text(" ".join(a.get_text(" ", strip=True) for a in node.find_all("a")))
        if not links_text:
            continue
        density = len(links_text) / max(len(total_text), 1)
        # Empirični prag: visoko razmerje link besedila običajno pomeni navigacijski šum.
        if density > 0.45:
            node.decompose()


def _extract_section_headings(soup: BeautifulSoup) -> list[str]:
    headings: list[str] = []
    for node in soup.find_all(["h2", "h3"]):
        text = _clean_text(node.get_text(" ", strip=True))
        if text and text not in headings:
            headings.append(text)
    return headings


def _extract_body_paragraphs(soup: BeautifulSoup) -> list[str]:
    """Zbere unikatne odstavke in filtrira trivialne kratke fragmente."""
    paragraphs: list[str] = []
    for p in soup.find_all("p"):
        text = _clean_text(p.get_text(" ", strip=True))
        if not text:
            continue
        if len(text) < 40:
            continue
        if text not in paragraphs:
            paragraphs.append(text)
    return paragraphs


def _compose_cleaned_text(
    *,
    title: str | None,
    section_headings: Iterable[str],
    body_paragraphs: Iterable[str],
) -> str:
    """Sestavi končni plain text.

    Format:
    - naslov najprej (boljši kontekst pri samostojnih chunkih),
    - odstavki ločeni z prazno vrstico (chunker-friendly).
    """
    lines: list[str] = []
    if title:
        lines.append(title.strip())

    heading_set = {heading.strip() for heading in section_headings if heading.strip()}
    for paragraph in body_paragraphs:
        text = paragraph.strip()
        if not text:
            continue
        if text in heading_set:
            continue
        lines.append(text)

    return "\n\n".join(lines).strip()


def _node_marker(node: Tag) -> str:
    if node.attrs is None:
        return node.name.lower()
    classes = node.get("class") or []
    class_text = " ".join(classes) if isinstance(classes, list) else str(classes)
    node_id = node.get("id") or ""
    return f"{node.name} {class_text} {node_id}".lower()


def _clean_text(value: str) -> str:
    """Normalizira whitespace brez posega v jezikovne znake."""
    value = value.replace("\xa0", " ")
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _dedupe_tags(tags: Iterable[Tag]) -> list[Tag]:
    seen: set[int] = set()
    unique: list[Tag] = []
    for tag in tags:
        key = id(tag)
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)
    return unique
