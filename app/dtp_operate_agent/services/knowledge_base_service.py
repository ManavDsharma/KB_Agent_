"""
Bedrock Knowledge Base retrieval.

One Knowledge Base holds three data sources (incidents, roster,
category matrix). When the data-source IDs are configured a search is
restricted to the relevant source, low-relevance and duplicate chunks
are dropped, and text is truncated so only a few useful lines reach
the model.
"""

from typing import Any, Dict, List, Optional

from config import (
    DEFAULT_KB_RESULTS,
    KB_DATA_SOURCE_IDS,
    KB_MAX_CHARS_PER_RESULT,
    KB_MIN_SCORE,
    KB_SEARCH_TYPE,
    KNOWLEDGE_BASE_ID,
    MAX_KB_RESULTS,
    get_kb_client,
)

from services.common import ServiceError, guarded, norm, truncate
from services.ids import find_issue_ref, find_ticket_ref

SOURCES = ("all", "incident", "roster", "category")

_DATA_SOURCE_KEY = "x-amz-bedrock-kb-data-source-id"

MAX_QUERY_CHARS = 500


class KnowledgeBaseService:

    def __init__(self, client=None):

        self._client = client

    @property
    def client(self):

        if self._client is None:
            self._client = get_kb_client()

        return self._client

    def _limit(self, number_of_results: Optional[int]) -> int:

        try:
            value = int(number_of_results or DEFAULT_KB_RESULTS)

        except (TypeError, ValueError):
            value = DEFAULT_KB_RESULTS

        return max(1, min(value, MAX_KB_RESULTS))

    @staticmethod
    def _source_filter(source: str) -> Optional[Dict[str, Any]]:

        data_source_id = KB_DATA_SOURCE_IDS.get(source)

        if not data_source_id:
            return None

        return {"equals": {"key": _DATA_SOURCE_KEY, "value": data_source_id}}

    @staticmethod
    def _reference(text: str) -> Optional[str]:
        """The ticket / issue ID a chunk is about, if it names one."""

        ticket = find_ticket_ref(text)

        if ticket:
            return f"{ticket[0]}{ticket[1]}"

        issue = find_issue_ref(text)

        return issue[0] if issue else None

    @guarded
    def search(
        self,
        query: str,
        source: str = "all",
        number_of_results: Optional[int] = None,
    ) -> Dict[str, Any]:

        text = " ".join(str(query or "").split())[:MAX_QUERY_CHARS]

        if not text:
            raise ServiceError("missing_fields", "A search query is required.")

        source = str(source or "all").strip().lower()

        if source not in SOURCES:
            raise ServiceError(
                "invalid_value",
                "source must be one of: " + ", ".join(SOURCES) + ".",
            )

        if not KNOWLEDGE_BASE_ID:
            raise ServiceError("unavailable", "The knowledge base is not configured.")

        wanted = self._limit(number_of_results)

        configuration: Dict[str, Any] = {
            # A little headroom, because low-score and duplicate
            # chunks are removed below.
            "numberOfResults": min(wanted + 3, MAX_KB_RESULTS + 3),
        }

        source_filter = None if source == "all" else self._source_filter(source)

        if source_filter:
            configuration["filter"] = source_filter

        if KB_SEARCH_TYPE in {"HYBRID", "SEMANTIC"}:
            configuration["overrideSearchType"] = KB_SEARCH_TYPE

        response = self.client.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={"text": text},
            retrievalConfiguration={"vectorSearchConfiguration": configuration},
        )

        seen = set()

        results: List[Dict[str, Any]] = []

        for hit in response.get("retrievalResults", []):

            score = hit.get("score")

            if score is not None and score < KB_MIN_SCORE:
                continue

            body = str(hit.get("content", {}).get("text", "")).strip()

            fingerprint = norm(body)[:160]

            if not body or fingerprint in seen:
                continue

            seen.add(fingerprint)

            row = {
                "score": round(score, 2) if score is not None else None,
                "text": truncate(" ".join(body.split()), KB_MAX_CHARS_PER_RESULT),
            }

            reference = self._reference(body)

            if reference:
                row["ref"] = reference

            results.append(row)

            if len(results) >= wanted:
                break

        payload: Dict[str, Any] = {
            "ok": True,
            "source": source,
            "count": len(results),
            "results": results,
        }

        if not results:
            payload["note"] = "No relevant knowledge base content was found."

        return payload
