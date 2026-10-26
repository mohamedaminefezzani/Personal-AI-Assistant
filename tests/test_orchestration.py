import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
import asyncio
from contextlib import suppress
from fastapi import HTTPException

from test_regressions import Pool, Cursor, ALICE, BOB
import create_agents
import storage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field
from langchain_core.tools import tool
from llm.graph import build_graph
from orchestration import WaitingApproval, decide
import tasks
import main


class DelegatingModel(BaseChatModel):
    specialist: bool = False
    execute_code: bool = False
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return 'orchestration-test'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        if isinstance(messages[-1], ToolMessage):
            answer = AIMessage(content='Coding complete' if self.specialist else 'Final report')
        elif self.execute_code:
            answer = AIMessage(content='', tool_calls=[{'name': 'run_code_tool', 'id': 'execute',
                'args': {'code': 'print("approved")', 'language': 'python'}}])
        elif self.specialist:
            answer = AIMessage(content='', tool_calls=[{'name': 'write_file_tool', 'id': 'write',
                'args': {'filename': 'example.py', 'content': 'print("approved")\n'}}])
        else:
            answer = AIMessage(content='', tool_calls=[{'name': 'delegate_to_coding', 'id': 'delegate',
                'args': {'objective': 'Write example.py', 'acceptance_criteria': ['Print approved']}}])
        return ChatResult(generations=[ChatGeneration(message=answer)])


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_code_execution_is_gated_without_executing_real_host_code(self):
        for decision in ('approve', 'reject'):
            calls = []
            @tool('run_code_tool')
            async def fake_execute(code: str, language: str = 'python') -> str:
                """Record a test command, without executing it."""
                calls.append(code)
                return 'approved'
            model = DelegatingModel(specialist=True, execute_code=True)
            with patch.object(create_agents, 'LLM', return_value=SimpleNamespace(model=model)), \
                 patch.object(create_agents, 'run_code_tool', fake_execute):
                coding = create_agents.init_coding_agent()
            graph = build_graph(coding, coding, coding, InMemorySaver())
            config = {'configurable': {'thread_id': 'alice-thread', 'pool': Pool(), 'user_id': ALICE}}
            result = await graph.ainvoke({'messages': [HumanMessage(content='Run code', id='user')],
                                        'use_coding_agent': True}, config)
            self.assertEqual(calls, [])
            approval = result['__interrupt__'][0]
            self.assertEqual(approval.value['args']['code'], 'print("approved")')
            await graph.ainvoke(Command(resume={approval.id: {
                'approval_id': approval.value['approval_id'], 'decision': decision}}), config)
            self.assertEqual(len(calls), 1 if decision == 'approve' else 0)

    async def test_task_runtime_returns_pending_approval_then_resumes_same_user_turn(self):
        with patch.object(create_agents, 'LLM', return_value=SimpleNamespace(model=DelegatingModel(specialist=True))):
            coding = create_agents.init_coding_agent()
        with patch.object(create_agents, 'LLM', return_value=SimpleNamespace(model=DelegatingModel())):
            principal = create_agents.init_main_agent(coding)
        state = SimpleNamespace(pool=Pool(), summariser_llm=None,
            graph=build_graph(principal, coding, principal, InMemorySaver()))
        args = {'app_state': state, 'task_id': 'job', 'thread_id': 'alice-thread',
                'user_id': ALICE, 'message': 'Write code'}
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, 'FILES_BASE_DIR', Path(directory)):
            pending = await main.run_agent_task(**args)
            self.assertIsInstance(pending, WaitingApproval)
            approval = pending.approvals[0]
            answer = await main.run_agent_task(**args, resume={approval['interrupt_id']: {
                'approval_id': approval['approval_id'], 'decision': 'approve'}})
            self.assertEqual(answer, 'Final report')
            self.assertEqual(storage.safe_path('example.py', ALICE).read_text(), 'print("approved")\n')
        human = [item for item in state.pool.history.values() if item['type'] == 'human']
        self.assertEqual(len(human), 1)

    async def test_worker_releases_job_while_waiting_instead_of_finishing_it(self):
        paused = asyncio.Event()
        approval = {'approval_id': 'proposal'}
        async def pause(*args):
            self.assertEqual(args[-1], [approval])
            paused.set()
        with patch.object(tasks, 'claim', AsyncMock(return_value=('job', 'thread', ALICE, {}))), \
             patch.object(tasks, 'recover_stale', AsyncMock()), patch.object(tasks, 'pause_task', side_effect=pause), \
             patch.object(tasks, 'finish', AsyncMock()) as finish:
            worker = asyncio.create_task(tasks.worker_loop(SimpleNamespace(pool=Pool()),
                AsyncMock(return_value=WaitingApproval([approval]))))
            await asyncio.wait_for(paused.wait(), 2)
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        finish.assert_not_awaited()

    async def test_decision_rejects_foreign_stale_and_duplicate_approvals(self):
        class ApprovalPool(Pool):
            status = 'waiting_approval'
            resumed = None

            async def execute(self, sql, params=()):
                query = ' '.join(sql.split())
                if query.startswith('SELECT status, pending_approvals'):
                    if params[1] != ALICE:
                        return Cursor()
                    return Cursor([(self.status, [{'approval_id': 'proposal', 'interrupt_id': 'pause', 'tool': 'write_file_tool'}])])
                if query.startswith('UPDATE task_runs'):
                    self.status = 'queued'
                    self.resumed = params[0].obj
                return Cursor()

        pool = ApprovalPool()
        for user, approval, status in [(BOB, 'proposal', 404), (ALICE, 'stale', 409)]:
            with self.assertRaises(HTTPException) as error:
                await decide(pool, 'job', user, approval, 'approve')
            self.assertEqual(error.exception.status_code, status)
        await decide(pool, 'job', ALICE, 'proposal', 'reject')
        self.assertEqual(pool.resumed, {'resume': {'pause': {'approval_id': 'proposal', 'decision': 'reject'}}})
        with self.assertRaises(HTTPException) as error:
            await decide(pool, 'job', ALICE, 'proposal', 'approve')
        self.assertEqual(error.exception.status_code, 409)

    async def exercise(self, decision, change_file=False, manual=False):
        main_model, code_model = DelegatingModel(), DelegatingModel(specialist=True)
        with patch.object(create_agents, 'LLM', return_value=SimpleNamespace(model=code_model)):
            coding = create_agents.init_coding_agent()
        with patch.object(create_agents, 'LLM', return_value=SimpleNamespace(model=main_model)):
            principal = create_agents.init_main_agent(coding)
        saver = InMemorySaver()
        graph = build_graph(principal, coding, principal, saver)
        config = {'configurable': {'thread_id': 'alice-thread', 'pool': Pool(), 'user_id': ALICE}}
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, 'FILES_BASE_DIR', Path(directory)):
            token = storage.file_user.set(ALICE)
            try:
                initial = await graph.ainvoke({'messages': [HumanMessage(content='Write code', id='user')],
                    'use_coding_agent': manual, 'video_frames': None}, config)
                self.assertIn('__interrupt__', initial)
                target = storage.safe_path('example.py')
                self.assertFalse(target.exists())
                pending = initial['__interrupt__'][0]
                self.assertIn('+print', pending.value['diff'])
                if change_file:
                    target.write_text('external change', encoding='utf-8')
                # Rebuild the outer graph against the same durable checkpoint state.
                graph = build_graph(principal, coding, principal, saver)
                result = await graph.ainvoke(Command(resume={pending.id: {
                    'approval_id': pending.value['approval_id'], 'decision': decision}}), config)
                self.assertNotIn('__interrupt__', result)
                if decision == 'approve' and not change_file:
                    self.assertEqual(target.read_text(), 'print("approved")\n')
                elif change_file:
                    self.assertEqual(target.read_text(), 'external change')
                else:
                    self.assertFalse(target.exists())
                self.assertEqual(result['messages'][-1].content, 'Coding complete' if manual else 'Final report')
                if not manual:
                    reports = [m.content for m in main_model.seen[-1] if isinstance(m, ToolMessage)]
                    self.assertIn('Coding complete', reports[-1])
                # Specialist instructions must not be archived as a new user turn.
                history = config['configurable']['pool'].history.values()
                self.assertFalse(any('Objective:' in str(item) for item in history))
            finally:
                storage.file_user.reset(token)

    async def test_automatic_delegation_pauses_before_write_and_returns_to_principal(self):
        await self.exercise('approve')

    async def test_rejection_never_writes_file(self):
        await self.exercise('reject')

    async def test_file_changed_during_approval_is_not_overwritten(self):
        await self.exercise('approve', change_file=True)

    async def test_manual_coding_shortcut_also_requires_approval(self):
        await self.exercise('approve', manual=True)
