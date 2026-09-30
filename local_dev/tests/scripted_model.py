"""
A Strands model that replays a script instead of calling Bedrock, so
the real agent loop (tools, hooks, session state) can be tested offline.
"""

import json
from typing import Any, Callable, Dict, List, Optional

from strands.models.model import Model


class ScriptedModel(Model):
    """
    Each script step is one model response:

        {"text": "final answer"}
        {"tools": [("get_incident", {"ticket_id": "INC0000258"}), ...]}

    When the script runs out `default` is used, so a runaway loop can be
    simulated with `default=lambda n: {"tools": [...]}`.
    """

    def __init__(
        self,
        script: List[Dict[str, Any]],
        default: Optional[Callable[[int], Dict[str, Any]]] = None,
    ):

        self.script = list(script)

        self.default = default or (lambda n: {"text": "done"})

        self.requests: List[Dict[str, Any]] = []

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):

        number = len(self.requests)

        self.requests.append(
            {
                "messages": len(messages),
                "system_blocks": kwargs.get("system_prompt_content"),
                "system_prompt": system_prompt,
                "tools": [spec["name"] for spec in (tool_specs or [])],
                "last_message": messages[-1] if messages else None,
            }
        )

        step = self.script.pop(0) if self.script else self.default(number)

        if "raise" in step:
            raise step["raise"]

        yield {"messageStart": {"role": "assistant"}}

        if "tools" in step:

            for index, (name, arguments) in enumerate(step["tools"]):

                yield {
                    "contentBlockStart": {
                        "start": {"toolUse": {"name": name, "toolUseId": f"t{number}-{index}"}}
                    }
                }

                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}}

                yield {"contentBlockStop": {}}

            stop = "tool_use"

        else:

            yield {"contentBlockStart": {"start": {}}}

            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}

            yield {"contentBlockStop": {}}

            stop = "end_turn"

        # A step may force a stop reason, e.g. "max_tokens" (output cut off).
        yield {"messageStop": {"stopReason": step.get("stop", stop)}}

        yield {
            "metadata": {
                "usage": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110},
                "metrics": {"latencyMs": 5},
            }
        }
