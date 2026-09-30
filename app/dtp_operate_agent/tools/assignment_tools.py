from strands import tool

from services import get_assignment_service
from tools.common import out


@tool
def find_handlers_for_issue(
    issue: str,
    date: str = "today",
) -> str:
    """Who should handle an issue or category? One call: category matrix ->
    owning team -> that team's availability (Y/N) on the date. If several
    teams match the result is `ambiguous`: ask the user which one.
    `issue` is an Issue-N ID or issue/category words."""

    return out(
        get_assignment_service().find_handlers(issue, date),
        ["members", "issues", "matches"],
    )
