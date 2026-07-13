"""Peshawar High Court profile."""
from .base import CourtProfile

PHC = CourtProfile(
    court_id="phc",
    source_website="https://www.peshawarhighcourt.gov.pk",
    base_url="https://www.peshawarhighcourt.gov.pk/PHCCMS/",
    search_page_url="https://www.peshawarhighcourt.gov.pk/PHCCMS/reportedJudgments.php",
    search_action_url="https://www.peshawarhighcourt.gov.pk/PHCCMS/reportedJudgments.php?action=search",
    court_name="Peshawar High Court",
    court_type="Peshawar High Court",
    citation_journal="PHC",
    s3_subfolder="PeshawarHighCourtJudgments",
    filename_prefix="Peshawar High Court - ",
    id_prefix="PHC",
    citation_pattern=r"^(\d{4})\s+PHC\s+(\d+)$",
)