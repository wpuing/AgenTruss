"""System prompt assembly. User text is never concatenated into system."""

from app.core.config import ROOT


def build_system(industry_name: str) -> str:
    """Merge industry template with common security rules."""
    industry = (ROOT / "templates" / industry_name / "system.txt").read_text(encoding="utf-8").strip()
    common = (ROOT / "templates" / "common" / "security_rules.txt").read_text(encoding="utf-8").strip()
    return f"{industry}\n{common}"
