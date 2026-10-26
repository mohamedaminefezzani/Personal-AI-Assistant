"""Recover a standalone web-search call leaked as text by a local model."""

import json
import re
from uuid import uuid4

from langchain_core.messages import AIMessage
from pydantic import ValidationError


def recover_search_call(message, tools):
    if not isinstance(message, AIMessage) or message.tool_calls or not isinstance(message.content, str):
        return message
    match = re.fullmatch(r'\s*(?:\[TOOL_CALLS\])?search_web_tool\[ARGS\](\{.*\})\s*',
                         message.content, re.DOTALL)
    if not match:
        return message
    search = next((tool for tool in tools if getattr(tool, 'name', None) == 'search_web_tool'), None)
    if search is None:
        return message
    try:
        args = json.loads(match.group(1))
        schema = search.get_input_schema()
        if not isinstance(args, dict) or set(args) - set(schema.model_fields):
            return message
        args = schema.model_validate(args).model_dump()
    except (ValueError, ValidationError):
        return message
    # Only web search is recovered. Never interpret prose, quoted examples, or
    # file/code actions as executable calls.
    return message.model_copy(update={
        'content': '',
        'tool_calls': [{'name': 'search_web_tool', 'args': args,
                        'id': str(uuid4()), 'type': 'tool_call'}],
    })
