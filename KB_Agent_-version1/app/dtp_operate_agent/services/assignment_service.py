"""
"Who should handle this?" - a deterministic multi-step lookup.

    issue / category text  ->  Category Matrix  ->  owning team
                           ->  Roster           ->  team availability

Doing this in Python means the model makes one tool call instead of
chaining three, and the team choice never depends on prompt wording.
"""

from typing import Any, Dict, List

from services.common import ServiceError, guarded
from services.ids import find_issue_ref


class AssignmentService:

    def __init__(self, catalog, roster):

        self.catalog = catalog

        self.roster = roster

    def _candidates(self, text: str) -> List[Dict[str, Any]]:

        ref = find_issue_ref(text)

        if ref:

            result = self.catalog.get_issue(ref[0])

            if not result.get("ok"):
                raise ServiceError(
                    result.get("error", "not_found"),
                    result.get("message", f"{ref[0]} was not found."),
                )

            return [result["issue"]]

        for query in (
            {"text": text},
            {"sub_category_name": text},
            {"category_name": text},
        ):

            result = self.catalog.search_issues(limit=6, **query)

            if not result.get("ok"):
                raise ServiceError(
                    result.get("error", "unavailable"),
                    result.get("message", "The category matrix is unavailable."),
                )

            if result["issues"]:
                return result["issues"]

        return []

    @guarded
    def find_handlers(
        self,
        issue: str,
        date: str = "today",
    ) -> Dict[str, Any]:

        text = str(issue or "").strip()

        if not text:
            raise ServiceError("missing_fields", "Which issue or category?")

        candidates = self._candidates(text)

        if not candidates:
            raise ServiceError(
                "not_found",
                f"No category or issue matches '{text}'.",
            )

        teams = sorted({item["team_id"] for item in candidates if item.get("team_id")})

        if not teams:
            raise ServiceError(
                "not_found",
                "The matching issue has no team assigned in the category matrix.",
            )

        if len(teams) > 1:

            return {
                "ok": True,
                "ambiguous": True,
                "message": "Several issues with different teams match. Which one?",
                "matches": candidates[:5],
            }

        availability = self.roster.get_team_availability(teams[0], date)

        result: Dict[str, Any] = {
            "ok": True,
            "team_id": teams[0],
            "issues": candidates[:3],
        }

        if availability.get("ok"):

            result["date"] = availability["date"]

            result["members"] = availability["members"]

        else:

            result["members_error"] = availability.get("message")

        return result
