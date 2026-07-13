"""Court profile — one file per court, selected via COURT_ID env."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CourtProfile:
    court_id: str
    source_website: str
    base_url: str
    search_page_url: str
    search_action_url: str
    court_name: str
    court_type: str
    citation_journal: str          # e.g. "PHC"
    s3_subfolder: str              # e.g. "PeshawarHighCourtJudgments"
    filename_prefix: str           # e.g. "Peshawar High Court - "
    id_prefix: str                   # e.g. "PHC" for synthetic row ids
    citation_pattern: str            # regex with groups (year, page)