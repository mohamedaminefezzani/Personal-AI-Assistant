import asyncio
from contextlib import suppress
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_regressions import ALICE, Pool, CaptureModel
import tasks
from errors import ConversationSummaryError
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from llm.context import update_summary, context_middleware


class SummaryFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_timeout_does_not_attempt_storage(self):
        async def stall(*args):
            await asyncio.Event().wait()
        llm = SimpleNamespace(ainvoke=stall)
        with patch('llm.context.SUMMARY_TIMEOUT_SECONDS', 0.01), \
             patch('llm.context.save_summary', AsyncMock()) as save:
            with self.assertRaisesRegex(ConversationSummaryError, 'generation timed out'):
                await update_summary(Pool(), 'thread', [HumanMessage(content='old', id='old')], llm, '')
        save.assert_not_awaited()

    async def test_summary_generation_failure_does_not_save_or_advance(self):
        llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError('Ollama failed')))
        with patch('llm.context.save_summary', AsyncMock()) as save:
            with self.assertRaisesRegex(ConversationSummaryError, 'generation failed'):
                await update_summary(Pool(), 'thread', [HumanMessage(content='old', id='old')], llm, 'previous')
        save.assert_not_awaited()

    async def test_summary_storage_timeout_is_bounded_and_identified(self):
        async def stall(*args):
            await asyncio.Event().wait()
        llm = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='summary')))
        with patch('llm.context.save_summary', side_effect=stall), \
             patch('llm.context.SUMMARY_SAVE_TIMEOUT_SECONDS', 0.01):
            with self.assertRaisesRegex(ConversationSummaryError, 'storage timed out'):
                await update_summary(Pool(), 'thread', [HumanMessage(content='old', id='old')], llm, '')

    async def test_empty_summary_is_not_saved(self):
        llm = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='')))
        with patch('llm.context.save_summary', AsyncMock()) as save:
            with self.assertRaisesRegex(ConversationSummaryError, 'generation failed'):
                await update_summary(Pool(), 'thread', [HumanMessage(content='old', id='old')], llm, '')
        save.assert_not_awaited()

    async def test_cancellation_is_not_converted_into_a_summary_failure(self):
        llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=asyncio.CancelledError))
        with self.assertRaises(asyncio.CancelledError):
            await update_summary(Pool(), 'thread', [HumanMessage(content='old', id='old')], llm, '')

    async def test_storage_failure_keeps_archive_and_old_summary_without_calling_main_model(self):
        pool, model = Pool(), CaptureModel(respond_with_tools=False)
        pool.summaries['thread'] = ('previous', None)
        llm = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='new summary')))
        agent = create_agent(model, tools=[], middleware=[context_middleware(lambda memory, summary: 'system' + summary)])
        messages = [HumanMessage(content='x' * 15000, id='old'), AIMessage(content='old answer', id='answer'),
                    HumanMessage(content='hello', id='new')]
        config = {'configurable': {'pool': pool, 'thread_id': 'thread', 'user_id': ALICE, 'summariser_llm': llm}}
        with patch('llm.context.CONTEXT_BUDGET', 3000), \
             patch('llm.context.save_summary', AsyncMock(side_effect=OSError('database unavailable'))):
            with self.assertRaisesRegex(ConversationSummaryError, 'storage failed'):
                await agent.ainvoke({'messages': messages}, config)
        self.assertFalse(model.seen)
        self.assertEqual(pool.summaries['thread'], ('previous', None))
        self.assertEqual(len(pool.history), 3)

    async def test_worker_persists_specific_summary_error_for_the_client(self):
        finished = asyncio.Event()
        async def finish(*args, **kwargs):
            self.assertIn('summary storage failed', kwargs['error'])
            finished.set()
        execute = AsyncMock(side_effect=ConversationSummaryError('Conversation summary storage failed.'))
        with patch.object(tasks, 'claim', AsyncMock(return_value=('job', 'thread', ALICE, {}))), \
             patch.object(tasks, 'recover_stale', AsyncMock()), patch.object(tasks, 'finish', side_effect=finish):
            worker = asyncio.create_task(tasks.worker_loop(SimpleNamespace(pool=object()), execute))
            try:
                await asyncio.wait_for(finished.wait(), 2)
            finally:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
