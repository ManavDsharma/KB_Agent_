from strands import tool
 
from services.knowledge_base_service import (
    KnowledgeBaseService,
)
 
 
# ============================================================
# SERVICE
# ============================================================
 
_service = KnowledgeBaseService()
 
 
# ============================================================
# SEARCH KNOWLEDGE BASE
# ============================================================
 
@tool
def search_knowledge_base(
    query: str,
    number_of_results: int = 5,
):
    """
    Search the Bedrock Knowledge Base using semantic retrieval.
    """
 
    return _service.search(
        query=query,
        number_of_results=number_of_results,
    )
 
 
# ============================================================
# FIND SIMILAR INCIDENTS
# ============================================================
 
@tool
def find_similar_incidents(
    query: str,
    number_of_results: int = 5,
):
    """
    Find incidents semantically similar to the supplied query.
    """
 
    enriched_query = (
        "Find incidents similar to the following incident "
        "or problem:\n"
        f"{query}"
    )
 
    return _service.search(
        query=enriched_query,
        number_of_results=number_of_results,
    )
 
 
# ============================================================
# FIND SIMILAR CATEGORIZATIONS
# ============================================================
 
@tool
def find_similar_categorizations(
    query: str,
    number_of_results: int = 5,
):
    """
    Find categorization records semantically similar to a query.
    """
 
    enriched_query = (
        "Find categorization records similar to the following "
        "issue:\n"
        f"{query}"
    )
 
    return _service.search(
        query=enriched_query,
        number_of_results=number_of_results,
    )
 
 