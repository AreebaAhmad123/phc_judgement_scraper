"""Query-side normalization for Pakistani legal-citation tokens, so BM25
keyword matching doesn't depend on whether the user typed a space.

Brief requirement (Stage 4, Section 3.1): "2026 PHC 153" and "2026PHC153"
should both match the same document.

Why this is query-side, not index-side: `citation_parser.py` already
stores citations in the ingested records as a clean spaced string (e.g.
"2026 PHC 4088" - see `parse_citation_fields`'s "Case Number" field), so
the corpus text itself is already consistent. The only variable is what
the *user* types into the search box - a single regex on the incoming
query is enough to normalize that, without needing a second index or a
custom Weaviate tokenizer/analyzer.

Kept as a small, dependency-free module (just `re`) so both
`retrieval.py` and `query_classifier.py`/eval code can import it without
pulling in anything heavier.
"""
import re

# <year><journal letters><page>, with zero or more spaces between each
# part - matches "2026 PHC 153", "2026PHC153", "2026 PHC153", etc. The
# \b anchors keep this from mangling unrelated 4-digit numbers that
# happen to be followed by letters for other reasons (rare in this
# corpus, but cheap to guard against).
_CITATION_TOKEN_RE = re.compile(r"\b(\d{4})\s*([A-Za-z]{2,6})\s*(\d{1,6})\b")


def normalize_citation_tokens(text: str) -> str:
    """Rewrites any citation-shaped token in `text` into a canonical
    '<year> <JOURNAL> <page>' form (journal upper-cased to match how
    citation_parser.py stores it). Leaves everything else untouched.
    Safe to call on arbitrary natural-language queries - most won't
    contain a matching token at all, in which case this is a no-op."""
    if not text:
        return text

    def _repl(match: "re.Match[str]") -> str:
        year, journal, page = match.groups()
        return f"{year} {journal.upper()} {page}"

    return _CITATION_TOKEN_RE.sub(_repl, text)


def looks_like_citation(text: str) -> bool:
    """True if the (normalized) query is essentially just a citation on
    its own, e.g. "2026 PHC 153" or "2026PHC153" - used to route these
    queries to a direct store lookup instead of a similarity search (see
    Brief Section 2: "this is a lookup, not a search")."""
    if not text:
        return False
    normalized = normalize_citation_tokens(text.strip())
    return bool(re.fullmatch(r"\d{4}\s+[A-Z]{2,6}\s+\d{1,6}", normalized))
