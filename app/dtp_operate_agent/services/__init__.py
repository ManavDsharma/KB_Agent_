"""
Service registry.

Services are created on first use, not at import, so the runtime
starts quickly and unit tests can substitute fakes with `set_service`.
"""

import threading

_lock = threading.RLock()

_instances = {}


def _get(name, factory):

    with _lock:

        if name not in _instances:
            _instances[name] = factory()

        return _instances[name]


def set_service(name, instance):
    """Install (or, with None, remove) a service instance. Used by tests."""

    with _lock:

        if instance is None:
            _instances.pop(name, None)

        else:
            _instances[name] = instance


def reset_services():

    with _lock:
        _instances.clear()


def get_categorization_service():

    def make():
        from services.categorization_service import CategorizationService

        return CategorizationService()

    return _get("categorization", make)


def get_roster_service():

    def make():
        from services.roster_service import RosterService

        return RosterService()

    return _get("roster", make)


def get_incident_service():

    def make():
        from services.incident_service import IncidentService

        return IncidentService(
            catalog=get_categorization_service(),
            people=get_roster_service(),
        )

    return _get("incident", make)


def get_knowledge_service():

    def make():
        from services.knowledge_base_service import KnowledgeBaseService

        return KnowledgeBaseService()

    return _get("knowledge", make)


def get_assignment_service():

    def make():
        from services.assignment_service import AssignmentService

        return AssignmentService(
            get_categorization_service(),
            get_roster_service(),
        )

    return _get("assignment", make)
