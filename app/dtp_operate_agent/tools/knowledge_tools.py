from typing import Literal

from strands import tool

from services import get_knowledge_service
from tools.common import out


@tool
def search_knowledge_base(
    query: str,
    source: Literal["all", "incident", "roster", "category"] = "all",
    max_results: int = 3,
) -> str:
    """Semantic search: definitions, explanations, FAQs, how-tos, similar past
    incidents and categorizations. Not for exact current values. Use the
    narrowest source: category (issues/teams), incident (past incidents and
    fixes), roster. Keep max_results small (1-8)."""

    return out(get_knowledge_service().search(query, source, max_results), ["results"])
