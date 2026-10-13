"""
Tool registry.

Tools are thin, typed wrappers around the services. Their definitions are
sent to the model on every call, so a request gets only the tools of the
domain it is about (see DOMAIN_TOOLS); "all" is the fallback for anything
ambiguous or cross-domain.
"""

MUTATING_TOOL_NAMES = frozenset(
    {
        "create_incident",
        "update_incident",
        "delete_incident",
        "update_issue",
        "set_employee_availability",
        "set_team_availability",
        "update_roster_entry",
        "create_roster_entry",
        "delete_roster_entry",
    }
)

_ROSTER = [
    "search_roster",
    "get_roster_entry",
    "get_employee_availability",
    "get_team_availability",
    "set_employee_availability",
    "set_team_availability",
    "update_roster_entry",
    "create_roster_entry",
    "delete_roster_entry",
    "search_knowledge_base",
]

_INCIDENT = [
    "get_incident",
    "search_incidents",
    "list_incidents_by_date",
    "list_sla_breaches",
    "create_incident",
    "update_incident",
    "delete_incident",
    # to suggest a valid category / find similar past incidents
    "search_category_matrix",
    "list_categories",
    "search_knowledge_base",
]

_CATEGORY = [
    "get_issue",
    "search_category_matrix",
    "list_categories",
    "update_issue",
    "search_knowledge_base",
    "find_handlers_for_issue",
]

# "Who should handle X?": issue -> team -> availability.
_HANDLERS = [
    "find_handlers_for_issue",
    "get_team_availability",
    "search_category_matrix",
    "get_issue",
    "search_roster",
    "search_knowledge_base",
]

DOMAIN_TOOLS = {
    "roster": _ROSTER,
    "incident": _INCIDENT,
    "category": _CATEGORY,
    "handlers": _HANDLERS,
}

DOMAINS = ("all", *DOMAIN_TOOLS)


def _every_tool():

    from tools import (
        assignment_tools,
        categorization_tools,
        incident_tools,
        knowledge_tools,
        roster_tools,
    )

    return [
        incident_tools.get_incident,
        incident_tools.search_incidents,
        incident_tools.list_incidents_by_date,
        incident_tools.list_sla_breaches,
        incident_tools.create_incident,
        incident_tools.update_incident,
        incident_tools.delete_incident,
        categorization_tools.get_issue,
        categorization_tools.search_category_matrix,
        categorization_tools.list_categories,
        categorization_tools.update_issue,
        roster_tools.search_roster,
        roster_tools.get_roster_entry,
        roster_tools.get_employee_availability,
        roster_tools.get_team_availability,
        roster_tools.set_employee_availability,
        roster_tools.set_team_availability,
        roster_tools.update_roster_entry,
        roster_tools.create_roster_entry,
        roster_tools.delete_roster_entry,
        knowledge_tools.search_knowledge_base,
        assignment_tools.find_handlers_for_issue,
    ]


def _slim_schema(node):
    """Drop what the model does not need from a JSON schema."""

    if isinstance(node, dict):

        slim = {}

        for key, value in node.items():

            # Strands fills every argument with "Parameter <name>", and every
            # optional one with "default": null. Both are pure noise.
            if key == "default" and value is None:
                continue

            if key == "description" and isinstance(value, str) and value.startswith("Parameter "):
                continue

            slim[key] = _slim_schema(value)

        return slim

    if isinstance(node, list):
        return [_slim_schema(item) for item in node]

    return node


def _slim_tool(tool):

    spec = dict(tool.tool_spec)

    spec["description"] = " ".join(str(spec.get("description", "")).split())

    spec["inputSchema"] = _slim_schema(spec["inputSchema"])

    tool.tool_spec = spec

    return tool


_slimmed = set()


def all_tools(domain: str = "all"):
    """The tools for a domain ("all" = every tool), with lean definitions."""

    tools = _every_tool()

    for tool in tools:

        if id(tool) not in _slimmed:
            _slim_tool(tool)
            _slimmed.add(id(tool))

    if domain == "all" or domain not in DOMAIN_TOOLS:
        return tools

    wanted = set(DOMAIN_TOOLS[domain])

    return [tool for tool in tools if tool.tool_spec["name"] in wanted]
