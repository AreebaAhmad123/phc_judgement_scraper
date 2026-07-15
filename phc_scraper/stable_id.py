"""Stable judgment identifier across runs."""
import hashlib

from .naming import pdf_leaf


def stable_judgment_id(pdf_url: str, decision_date: str | None, case_info: str) -> str:
    leaf = pdf_leaf(pdf_url or "")
    basis = f"{leaf}|{decision_date or ''}|{(case_info or '').strip()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]