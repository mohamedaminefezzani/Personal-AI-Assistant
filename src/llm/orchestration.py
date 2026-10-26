"""Sequential delegation and approval gates around tools, before side effects."""

import difflib
import hashlib
import json
import asyncio

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.config import get_config
from langgraph.types import interrupt

from orchestration import emit
from storage import safe_path
from llm.responses import assistant_answer


def approval_proposal(call):
    args, name = call['args'], call['name']
    proposal = {'tool': name, 'args': args}
    if name == 'run_code_tool':
        proposal['notice'] = 'This code runs on the server host, outside a sandbox.'
    if name == 'write_file_tool':
        path = safe_path(args['filename'])
        before = path.read_bytes() if path.exists() else None
        if before is not None and len(before) > 100_000:
            raise ValueError('File too large for an approval diff')
        previous = before.decode('utf-8') if before is not None else ''
        after = (previous if args.get('mode') == 'append' else '') + args['content']
        proposal['before_hash'] = hashlib.sha256(before).hexdigest() if before is not None else None
        proposal['resolved_path'] = str(path)
        proposal['diff'] = ''.join(difflib.unified_diff(previous.splitlines(True), after.splitlines(True),
                                                       fromfile=args['filename'], tofile=args['filename']))
    fingerprint = json.dumps({'call_id': call['id'], **proposal}, sort_keys=True)
    proposal['approval_id'] = hashlib.sha256(fingerprint.encode()).hexdigest()
    return proposal


def tool_progress(agent_name):
    @wrap_tool_call
    async def progress(request, handler):
        cfg = get_config()
        call = request.tool_call
        label = {'agent': agent_name, 'tool': call['name']}
        key = agent_name + ':' + call['id']
        if call['name'] == 'delegate_to_coding':
            messages = request.state['messages']
            last_user = max((i for i, m in enumerate(messages) if m.type == 'human'), default=0)
            count = sum(c['name'] == 'delegate_to_coding' for m in messages[last_user:]
                        for c in getattr(m, 'tool_calls', []))
            if count > 3:
                return ToolMessage(content='Delegation limit reached. Report the current result to the user.',
                                   tool_call_id=call['id'], status='error')
        if call['name'] in ('write_file_tool', 'run_code_tool'):
            proposal = approval_proposal(call)
            await emit(cfg, 'approval_requested', {**label, 'proposal': proposal},
                       key + ':approval:' + proposal['approval_id'])
            decision = interrupt(proposal)
            if (not isinstance(decision, dict) or decision.get('decision') != 'approve'
                    or decision.get('approval_id') != proposal['approval_id']):
                await emit(cfg, 'action_rejected', label, key + ':rejected')
                return ToolMessage(content='Action rejected or file changed since approval. Do not retry without user instructions.',
                                   tool_call_id=call['id'], status='error')
            # Recheck the file immediately before executing the approved write.
            if approval_proposal(call)['approval_id'] != proposal['approval_id']:
                return ToolMessage(content='File changed; prepare a new proposal.', tool_call_id=call['id'], status='error')
        await emit(cfg, 'tool_started', label, key + ':start')
        result = await handler(request)
        await emit(cfg, 'tool_completed', {**label, 'status': getattr(result, 'status', 'success')}, key + ':end')
        return result
    return progress


def coding_delegate(coding_agent):
    execution_lock = asyncio.Lock()
    @tool
    async def delegate_to_coding(objective: str, acceptance_criteria: list[str]) -> str:
        """Delegate a bounded programming task to the coding specialist, then receive its report."""
        cfg = get_config()
        # Internal conversation stays in the nested checkpoint, not the user's archive.
        cfg = {**cfg, 'configurable': {**cfg.get('configurable', {}), 'is_delegation': True}}
        task = 'Objective:\n' + objective + '\nAcceptance criteria:\n' + '\n'.join(acceptance_criteria)
        await emit(cfg, 'delegation_started', {'agent': 'coding_agent', 'objective': objective},
                   'delegation:' + hashlib.sha256(task.encode()).hexdigest())
        async with execution_lock:
            result = await coding_agent.ainvoke({'messages': [HumanMessage(content=task,
                id='mission:' + hashlib.sha256(task.encode()).hexdigest())]}, cfg)
        answer = assistant_answer(result['messages'])
        if not answer:
            raise RuntimeError('Coding agent returned no completed report')
        await emit(cfg, 'delegation_completed', {'agent': 'coding_agent'},
                   'delegation-done:' + hashlib.sha256(task.encode()).hexdigest())
        return answer
    return delegate_to_coding
