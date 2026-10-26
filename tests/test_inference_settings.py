import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import config
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages import ToolMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain.agents import create_agent
from langchain_core.tools import tool
from llm.context import model_input_budget, messages_token_count, summarise_messages, select_context
from llm.init_llm import LLM
from llm.tools import search_web_tool
from llm.tool_calls import recover_search_call
from llm.context import context_middleware


class TextToolModel(BaseChatModel):
    @property
    def _llm_type(self):
        return 'text-tool-regression'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        content = ('Voici les nouvelles et leurs sources.' if isinstance(messages[-1], ToolMessage)
                   else 'search_web_tool[ARGS]{"query":"latest news last 7 days","topic":"news","max_results":5,"time_range":"week"}')
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


class InferenceSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_video_context_has_its_own_input_budget(self):
        text = SimpleNamespace(num_ctx=8192, num_predict=1024)
        video = SimpleNamespace(num_ctx=32768, num_predict=1024)
        self.assertEqual(model_input_budget(text), 6656)
        self.assertEqual(model_input_budget(video), 31232)
        images = HumanMessage(content=[{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,test'}}] * 10)
        selected, _ = select_context([images], 'Describe frames', context_budget=model_input_budget(video))
        self.assertEqual(selected, [images])

    async def test_summary_prompt_fits_summary_model_and_keeps_latest_data(self):
        llm = SimpleNamespace(num_ctx=8192, num_predict=1024,
                              ainvoke=AsyncMock(return_value=AIMessage(content='summary')))
        messages = [HumanMessage(content='x' * 4000) for _ in range(50)]
        messages.append(HumanMessage(content='Latest decision: use the smaller model.'))
        await summarise_messages(messages, llm, existing='s' * 6000)
        prompt = llm.ainvoke.call_args.args[0]
        self.assertLessEqual(messages_token_count(prompt), model_input_budget(llm))
        self.assertIn('Latest decision', prompt[0].content)

    def test_model_constructor_receives_timeouts_and_video_override(self):
        with patch('llm.init_llm.ChatOllama') as constructor:
            LLM(llm='test-model', num_ctx=32768)
        settings = constructor.call_args.kwargs
        self.assertEqual(settings['num_ctx'], 32768)
        self.assertEqual(settings['num_predict'], config.OLLAMA_NUM_PREDICT)
        self.assertEqual(settings['async_client_kwargs']['timeout'], config.OLLAMA_HTTP_TIMEOUT_SECONDS)
        self.assertEqual(settings['client_kwargs']['timeout'], config.OLLAMA_HTTP_TIMEOUT_SECONDS)

    def test_summary_output_limit_can_differ_from_main_model(self):
        with patch('llm.init_llm.ChatOllama') as constructor:
            LLM(llm='summary-model', num_predict=512)
        self.assertEqual(constructor.call_args.kwargs['num_predict'], 512)

    async def test_search_limits_content_and_preserves_sources(self):
        results = [{'title': f'Source {i}', 'url': f'https://example.com/{i}',
                    'content': 'x' * 5000, 'raw_content': 'y' * 10000} for i in range(5)]
        with patch('llm.tools.TavilySearch') as factory:
            factory.return_value.ainvoke = AsyncMock(return_value={'results': results})
            output = json.loads(await search_web_tool.ainvoke({'query': 'test', 'max_results': 5, 'time_range': 'week'}))
        self.assertEqual(factory.return_value.ainvoke.call_args.args[0], {'query': 'test', 'time_range': 'week'})
        self.assertEqual(factory.call_args.kwargs['max_results'], config.SEARCH_MAX_RESULTS)
        self.assertEqual(len(output['results']), config.SEARCH_MAX_RESULTS)
        self.assertEqual(output['results'][0]['url'], 'https://example.com/0')
        self.assertLessEqual(len(output['results'][0]['content']), config.SEARCH_RESULT_CHARS)
        self.assertNotIn('raw_content', output['results'][0])

    def test_standalone_search_text_is_recovered_and_native_calls_are_unchanged(self):
        for prefix in ('', '[TOOL_CALLS]'):
            original = AIMessage(content=prefix + 'search_web_tool[ARGS]{"query":"news","topic":"news"}')
            repaired = recover_search_call(original, [search_web_tool])
            self.assertEqual(repaired.content, '')
            self.assertEqual(repaired.tool_calls[0]['args']['query'], 'news')
            self.assertIs(recover_search_call(repaired, [search_web_tool]), repaired)
        self.assertIs(recover_search_call(original, []), original)

    def test_quoted_invalid_or_action_text_is_never_executed(self):
        for content in (
            'Example: search_web_tool[ARGS]{"query":"news"}',
            '```\nsearch_web_tool[ARGS]{"query":"news"}\n```',
            'search_web_tool[ARGS]{"query":"news","unexpected":true}',
            'search_web_tool[ARGS]{"topic":"news"}',
            'search_web_tool[ARGS]{invalid}',
            'search_web_tool[ARGS]{"query":"news","time_range":"invalid"}',
            'run_code_tool[ARGS]{"code":"print(1)"}',
        ):
            original = AIMessage(content=content)
            self.assertIs(recover_search_call(original, [search_web_tool]), original)

    async def test_recovered_call_runs_tool_and_continues_to_final_answer(self):
        calls = []

        @tool('search_web_tool', args_schema=search_web_tool.get_input_schema())
        async def fake_search(**kwargs):
            """Return test news without network access."""
            calls.append(kwargs)
            return 'News source: https://example.com/news'

        agent = create_agent(model=TextToolModel(), tools=[fake_search],
                             middleware=[context_middleware(lambda memory, summary: 'Search and summarise news.')])
        result = await agent.ainvoke({'messages': [HumanMessage(content='Les nouvelles des 7 derniers jours')]})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['time_range'], 'week')
        self.assertTrue(any(isinstance(message, ToolMessage) for message in result['messages']))
        self.assertEqual(result['messages'][-1].content, 'Voici les nouvelles et leurs sources.')
