from config import (
    AWS_REGION,
    KNOWLEDGE_BASE_ID,
    DEFAULT_KB_RESULTS,
    MAX_KB_RESULTS,
    get_boto3_session,
)
 
 
class KnowledgeBaseService:
 
    def __init__(self):
 
        session = get_boto3_session()
 
        self.client = session.client(
            "bedrock-agent-runtime",
            region_name=AWS_REGION,
        )
 
    # ========================================================
    # RESULT LIMIT
    # ========================================================
 
    def _limit(
        self,
        number_of_results,
    ):
 
        return max(
            1,
            min(
                int(number_of_results),
                MAX_KB_RESULTS,
            ),
        )
 
    # ========================================================
    # RETRIEVE
    # ========================================================
 
    def retrieve(
        self,
        query,
        number_of_results=DEFAULT_KB_RESULTS,
    ):
 
        query = str(
            query
        ).strip()
 
        if not query:
 
            raise ValueError(
                "Knowledge Base query "
                "cannot be empty."
            )
 
        number_of_results = self._limit(
            number_of_results
        )
 
        response = self.client.retrieve(
 
            knowledgeBaseId=(
                KNOWLEDGE_BASE_ID
            ),
 
            retrievalQuery={
                "text": query
            },
 
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": (
                        number_of_results
                    )
                }
            },
        )
 
        return response.get(
            "retrievalResults",
            [],
        )
 
    # ========================================================
    # FORMAT RESULTS
    # ========================================================
 
    def format_results(
        self,
        results,
    ):
 
        formatted = []
 
        for index, result in enumerate(
            results,
            start=1,
        ):
 
            content = result.get(
                "content",
                {},
            )
 
            metadata = result.get(
                "metadata",
                {},
            )
 
            formatted.append(
                {
                    "rank": index,
                    "score": result.get(
                        "score"
                    ),
                    "text": content.get(
                        "text",
                        "",
                    ),
                    "document_id": result.get(
                        "documentId"
                    ),
                    "metadata": metadata,
                }
            )
 
        return formatted
 
    # ========================================================
    # SEARCH
    # ========================================================
 
    def search(
        self,
        query,
        number_of_results=DEFAULT_KB_RESULTS,
    ):
 
        results = self.retrieve(
            query=query,
            number_of_results=number_of_results,
        )
 
        formatted = self.format_results(
            results
        )
 
        return {
            "query": query,
            "count": len(formatted),
            "results": formatted,
        }
 
 