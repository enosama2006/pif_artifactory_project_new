"""Shared lexicon: tokens too generic to carry identity.

Used by inventory merge (to drop all-generic pseudo-actors and sanitize
placeholders) and by the residual leak sweep (to suppress noise). Extend it
from run findings; every addition is a deliberate judgment that the word can
never, alone, identify the organisation.
"""

GENERIC_TOKENS = {
    # articles / pronouns / glue
    "the", "a", "an", "of", "and", "for", "its", "this", "that", "their",
    "all", "other", "with", "across",
    # document / governance vocabulary
    "data", "governance", "policy", "policies", "document", "documents",
    "sheet", "register", "registers", "appendix", "appendices", "table",
    "contents", "list", "laws", "law", "regulations", "regulation",
    "regulatory", "applicable", "issue", "tracking", "definitions",
    "abbreviations", "version", "approval", "history", "amendment",
    "framework", "plan", "map", "curriculum", "training", "strategy",
    "strategic", "standards", "standard", "principles", "guidelines",
    "processes", "procedures", "program", "roadmap", "artifacts",
    # org-structure vocabulary
    "department", "departments", "committee", "committees", "board",
    "management", "office", "officer", "officers", "center", "centre",
    "division", "divisions", "group", "working", "steering", "advisory",
    "national", "corporate", "affairs", "administration", "records",
    "body", "bodies", "head", "chief", "staff", "unit", "units",
    "general", "executive", "internal", "external",
    # people / role vocabulary (generic standard roles are not identity)
    "owner", "owners", "steward", "stewards", "custodian", "custodians",
    "consumer", "consumers", "personnel", "employees", "stakeholders",
    "advisor", "officer",
    # domain vocabulary
    "analytics", "analytic", "advanced", "ai", "risk", "catalog",
    "catalogue", "metadata", "quality", "sharing", "reference", "master",
    "operations", "architecture", "modeling", "interoperability",
    "classification", "personal", "protection", "security", "domain",
    "domains", "functional", "ownership", "systems", "system", "tools",
    "premises", "wide", "level", "hub", "change", "performance",
    "reporting", "compliance", "monitoring", "value", "realization",
    "intelligence", "business", "content", "freedom", "information",
    "open", "access",
}
