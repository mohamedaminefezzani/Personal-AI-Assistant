"""Build one visible answer from the assistant/tool exchanges in a user turn."""

from langchain_core.messages import AIMessage
import html
from pathlib import PurePath
import re


FILE_LANGUAGES = {
    '.py': 'python', '.js': 'javascript', '.ts': 'typescript', '.ps1': 'powershell',
    '.bat': 'bat', '.cmd': 'bat', '.sh': 'bash', '.html': 'html', '.css': 'css',
    '.json': 'json', '.md': 'markdown', '.yaml': 'yaml', '.yml': 'yaml',
    '.sql': 'sql', '.go': 'go', '.rs': 'rust', '.java': 'java', '.txt': 'text',
}


def written_file_block(call, result):
    if call.get('name') != 'write_file_tool' or result is None:
        return ''
    if getattr(result, 'status', 'success') != 'success':
        return ''
    confirmation = text_content(result.content)
    if not confirmation.startswith(("Wrote '", "Appended to '")):
        return ''
    args = call.get('args', {})
    filename, code = args.get('filename'), args.get('content')
    if not isinstance(filename, str) or not isinstance(code, str) or not code:
        return ''
    # A fence longer than any run in the file preserves embedded Markdown fences.
    fence = '`' * max(3, max((len(run) + 1 for run in re.findall(r'`+', code)), default=3))
    language = FILE_LANGUAGES.get(PurePath(filename).suffix.lower(), 'text')
    label = html.escape(filename.replace('\n', ' ').replace('\r', ' '))
    label = re.sub(r'([\\`*_[\]])', r'\\\1', label)
    body = code if code.endswith('\n') else code + '\n'
    return f'### File: {label}\n\n{fence}{language}\n{body}{fence}'


def text_content(content):
    if isinstance(content, str):
        return content
    return "\n".join(
        block["text"] for block in content
        if isinstance(block, dict) and block.get("type") in ("text", "output_text")
        and isinstance(block.get("text"), str)
    )


def assistant_answer(messages):
    assistant = [message for message in messages if message.type == "ai"]
    if not assistant or assistant[-1].tool_calls:
        return ""
    results = {message.tool_call_id: message for message in messages if message.type == 'tool'}
    visible = '\n\n'.join(text_content(message.content) for message in assistant)
    existing_code = {match.group(2).strip() for match in re.finditer(
        r'(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)^[ \t]*\1[ \t]*(?:\n|$)', visible,
    )}
    parts = []
    shown = set()
    for message in assistant:
        parts.append(text_content(message.content).strip())
        for call in message.tool_calls:
            block = written_file_block(call, results.get(call.get('id')))
            if not block:
                continue
            args = call['args']
            key = (args['filename'], args['content'])
            if key not in shown and args['content'].strip() not in existing_code:
                parts.append(block)
                shown.add(key)
    return "\n\n".join(part for part in parts if part)


def current_turn_answer(messages, user_message_id):
    start = next((index for index, message in enumerate(messages)
                  if message.type == "human" and message.id == user_message_id), None)
    if start is None:
        raise RuntimeError("Current user message is missing from the agent result")
    end = next((index for index in range(start + 1, len(messages))
                if messages[index].type == "human"), len(messages))
    answer = assistant_answer(messages[start + 1:end])
    if not answer:
        raise RuntimeError("Agent did not produce a completed text response")
    return answer


def display_messages(messages):
    """Group archived responses without changing the raw model/tool archive."""
    pending = []
    for message in messages:
        if message.type == "human":
            answer = assistant_answer(pending)
            if answer:
                yield AIMessage(content=answer)
            pending = []
            yield message
        elif message.type in ("ai", "tool"):
            pending.append(message)
    answer = assistant_answer(pending)
    if answer:
        yield AIMessage(content=answer)
