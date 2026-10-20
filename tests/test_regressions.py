import asyncio
from contextlib import asynccontextmanager, suppress
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

os.environ["JWT_SECRET"] = "regression-test-secret-not-for-production"
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-test"
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-test"
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field, ValidationError

import main
import create_agents
import storage
import tasks
from db.history import archive_messages, load_messages
from llm.context import select_context, messages_token_count, context_middleware
from llm.graph import build_graph
from llm.responses import current_turn_answer, display_messages
from llm.tools import write_file_tool, read_file_tool, list_files_tool


ALICE = str(uuid.UUID(int=1))
BOB = str(uuid.UUID(int=2))


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.rowcount = len(self.rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class Pool:
    def __init__(self):
        self.owners = {"alice-thread": ALICE, "bob-thread": BOB}
        self.history = {}
        self.queries = []
        self.summaries = {}
        self.jobs = {}

    @asynccontextmanager
    async def connection(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    @asynccontextmanager
    async def cursor(self):
        pool = self
        class Adapter:
            async def execute(self, sql, params=()):
                self.result = await pool.execute(sql, params)

            async def fetchall(self):
                return await self.result.fetchall()
        yield Adapter()

    async def execute(self, sql, params=()):
        query = " ".join(sql.split())
        self.queries.append((query, params))
        if query.startswith("SELECT 1 FROM conversations"):
            return Cursor([(1,)] if self.owners.get(params[0]) == params[1] else [])
        if query.startswith("INSERT INTO conversation_messages"):
            self.history.setdefault((params[0], params[1]), params[2].obj)
        if query.startswith("SELECT payload FROM conversation_messages"):
            return Cursor([(payload,) for (thread, _), payload in self.history.items() if thread == params[0]])
        if query.startswith("SELECT summary,"):
            return Cursor([self.summaries[params[0]]]) if params[0] in self.summaries else Cursor()
        if query.startswith("INSERT INTO conversation_summaries"):
            self.summaries[params[0]] = (params[1], params[2])
        if query.startswith("SELECT status, output, error FROM task_runs"):
            job = self.jobs.get(params[0])
            return Cursor([job[:3]]) if job and job[3] == params[1] else Cursor()
        return Cursor()


class CaptureModel(BaseChatModel):
    seen: list = Field(default_factory=list)
    respond_with_tools: bool = True

    @property
    def _llm_type(self):
        return "regression-capture"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        if not self.respond_with_tools or isinstance(messages[-1], ToolMessage):
            answer = AIMessage(content="done")
        else:
            answer = AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "hello"}, "id": "call"}])
        return ChatResult(generations=[ChatGeneration(message=answer)])


@tool
def echo(text: str) -> str:
    """Return the supplied text."""
    return text


class ContextTests(unittest.TestCase):
    def test_trim_preserves_complete_tool_turns_and_latest_user(self):
        messages = [HumanMessage(content="x" * 60_000, id="old"), AIMessage(content="old answer", id="old-ai"),
                    HumanMessage(content="new", id="new"),
                    AIMessage(content="", id="call-ai", tool_calls=[{"name": "echo", "args": {}, "id": "c"}]),
                    ToolMessage(content="result", tool_call_id="c", id="reply")]
        with patch("llm.context.CONTEXT_BUDGET", 1000):
            kept, dropped = select_context(messages, "system")
        self.assertEqual([m.id for m in kept], ["new", "call-ai", "reply"])
        self.assertEqual([m.id for m in dropped], ["old", "old-ai"])

    def test_huge_current_turn_is_rejected_before_inference(self):
        with self.assertRaisesRegex(ValueError, "context budget"):
            select_context([HumanMessage(content="x" * 100_000)], "system")

    def test_images_and_tool_arguments_are_counted(self):
        image = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}])
        self.assertGreater(messages_token_count([image]), 1000)
        call = AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "x" * 1000}, "id": "c"}])
        self.assertGreater(messages_token_count([call]), 300)


class ResponseTests(unittest.TestCase):
    def file_turn(self, code, *, result="Wrote 'wake_up.ps1' (12 chars).", status='success', final='Option 2'):
        messages = [HumanMessage(content='question', id='current'),
                    AIMessage(content='Option 1', tool_calls=[{
                        'name': 'write_file_tool', 'args': {'filename': 'wake_up.ps1', 'content': code}, 'id': 'write',
                    }])]
        if result is not None:
            messages.append(ToolMessage(content=result, tool_call_id='write', status=status))
        messages.append(AIMessage(content=final))
        return messages

    def test_successful_file_content_is_shown_between_answer_parts_and_in_history(self):
        messages = self.file_turn('Write-Host "Hello"\n')
        answer = current_turn_answer(messages, 'current')
        self.assertIn('```powershell\nWrite-Host "Hello"\n```', answer)
        self.assertLess(answer.index('Option 1'), answer.index('Write-Host'))
        self.assertLess(answer.index('Write-Host'), answer.index('Option 2'))
        self.assertEqual(list(display_messages(messages))[-1].content, answer)

    def test_failed_or_unconfirmed_file_writes_do_not_claim_to_create_a_file(self):
        for params in ({'result': None}, {'result': 'Error writing file: access denied'},
                       {'status': 'error'}):
            with self.subTest(params=params):
                answer = current_turn_answer(self.file_turn('secret code', **params), 'current')
                self.assertEqual(answer, 'Option 1\n\nOption 2')

    def test_embedded_fences_in_generated_file_cannot_close_the_outer_code_block(self):
        answer = current_turn_answer(self.file_turn('```python\nprint(1)\n```\n'), 'current')
        self.assertIn('````powershell\n```python\nprint(1)\n```\n````', answer)

    def test_file_content_already_present_in_answer_is_not_duplicated(self):
        answer = current_turn_answer(self.file_turn('Write-Host "Hello"',
                                     final='```powershell\nWrite-Host "Hello"\n```'), 'current')
        self.assertEqual(answer.count('Write-Host "Hello"'), 1)

    def test_answer_includes_text_before_and_after_tool_execution(self):
        messages = [HumanMessage(content="old question", id="old"), AIMessage(content="old answer"),
                    HumanMessage(content="current question", id="current"),
                    AIMessage(content="Option 1", tool_calls=[{"name": "echo", "args": {}, "id": "call"}]),
                    ToolMessage(content="internal result", tool_call_id="call"), AIMessage(content="Option 2")]
        self.assertEqual(current_turn_answer(messages, "current"), "Option 1\n\nOption 2")

    def test_multiple_tool_cycles_preserve_order_and_skip_empty_messages(self):
        messages = [HumanMessage(content="question", id="current"),
                    AIMessage(content="First", tool_calls=[{"name": "echo", "args": {}, "id": "a"}]),
                    ToolMessage(content="internal A", tool_call_id="a"),
                    AIMessage(content="", tool_calls=[{"name": "echo", "args": {}, "id": "b"}]),
                    ToolMessage(content="internal B", tool_call_id="b"), AIMessage(content="Last")]
        self.assertEqual(current_turn_answer(messages, "current"), "First\n\nLast")

    def test_only_visible_text_blocks_are_included(self):
        messages = [HumanMessage(content="question", id="current"),
                    AIMessage(content=[{"type": "reasoning", "text": "private reasoning"},
                                       {"type": "text", "text": "Visible first"},
                                       {"type": "text", "text": "Visible second"}])]
        self.assertEqual(current_turn_answer(messages, "current"), "Visible first\nVisible second")

    def test_answer_never_spills_into_another_user_turn(self):
        messages = [HumanMessage(content="first", id="first"), AIMessage(content="answer one"),
                    HumanMessage(content="second", id="second"), AIMessage(content="answer two")]
        self.assertEqual(current_turn_answer(messages, "first"), "answer one")
        with self.assertRaisesRegex(RuntimeError, "missing"):
            current_turn_answer(messages, "unknown")

    def test_unfinished_tool_turn_is_not_displayed_as_a_completed_answer(self):
        messages = [HumanMessage(content="question", id="current"),
                    AIMessage(content="Working", tool_calls=[{"name": "echo", "args": {}, "id": "call"}]),
                    ToolMessage(content="internal result", tool_call_id="call")]
        with self.assertRaisesRegex(RuntimeError, "completed"):
            current_turn_answer(messages, "current")
        self.assertEqual([m.type for m in display_messages(messages)], ["human"])


class FileTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_users_have_separate_files_and_tool_contexts(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, "FILES_BASE_DIR", Path(directory)):
            async def write_as(user, text):
                token = storage.file_user.set(user)
                try:
                    await write_file_tool.ainvoke({"filename": "notes.txt", "content": text})
                    return await read_file_tool.ainvoke({"filename": "notes.txt"})
                finally:
                    storage.file_user.reset(token)
            self.assertEqual(await asyncio.gather(write_as(ALICE, "alice secret"), write_as(BOB, "bob secret")),
                             ["alice secret", "bob secret"])
            self.assertNotEqual(storage.safe_path("notes.txt", ALICE), storage.safe_path("notes.txt", BOB))
            for path in ["../" + BOB + "/notes.txt", str(Path(directory) / BOB / "notes.txt")]:
                with self.assertRaises(ValueError):
                    storage.safe_path(path, ALICE)
            self.assertIn("authenticated", await list_files_tool.ainvoke({}))

    async def test_downloads_use_the_authenticated_namespace(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, "FILES_BASE_DIR", Path(directory)):
            storage.safe_path("secret.txt", ALICE).write_text("secret", encoding="utf-8")
            transport = httpx.ASGITransport(app=main.app)
            main.app.dependency_overrides[main.get_current_user] = lambda: {"id": BOB, "username": "bob"}
            try:
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/files/secret.txt")
                    self.assertEqual(response.status_code, 404)
                    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": ALICE, "username": "alice"}
                    response = await client.get("/files/secret.txt")
                    self.assertEqual(response.text, "secret")
                    self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            finally:
                main.app.dependency_overrides.clear()


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = Pool()
        main.app.state.pool = self.pool
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": ALICE, "username": "alice"}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        main.app.dependency_overrides.clear()

    async def test_foreign_conversation_rejected_on_all_entry_points(self):
        requests = [
            ("POST", "/chat", {"message": "hello", "thread_id": "bob-thread"}),
            ("GET", "/conversations/bob-thread/messages", None),
            ("PATCH", "/conversations/bob-thread", {"title": "hijacked"}),
            ("DELETE", "/conversations/bob-thread", None),
            ("GET", "/chat/status/bob-thread", None),
            ("GET", "/chat/stream/foreign-task", None),
        ]
        for method, route, body in requests:
            response = await self.client.request(method, route, json=body)
            self.assertEqual(response.status_code, 404, route)
        self.assertFalse(any(q.startswith("INSERT INTO task_runs") for q, _ in self.pool.queries))

    async def test_archive_survives_a_pruned_or_missing_graph_checkpoint(self):
        await archive_messages(self.pool, "alice-thread", [HumanMessage(content="old message", id="old"),
                                                         AIMessage(content="old answer", id="old-ai")])
        # No graph is needed to read archived history.
        main.app.state.graph = None
        response = await self.client.get("/conversations/alice-thread/messages")
        self.assertEqual([m["content"] for m in response.json()], ["old message", "old answer"])

    async def test_enqueue_persists_input_and_locks_owned_conversation(self):
        response = await self.client.post("/chat", json={"message": "hello", "thread_id": "alice-thread"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("FOR UPDATE" in q for q, _ in self.pool.queries))
        insert = next(params for query, params in self.pool.queries if query.startswith("INSERT INTO task_runs"))
        self.assertEqual(insert[3].obj["message"], "hello")

    async def test_graph_runs_tools_with_one_fresh_system_prompt_per_call(self):
        model = CaptureModel()
        agent = create_agent(model, tools=[echo], system_prompt="base", name="main_agent",
                             middleware=[context_middleware(lambda memory, summary: "dynamic" + memory + summary)])
        graph = build_graph(agent, agent, agent, InMemorySaver())
        config = {"configurable": {"thread_id": "alice-thread", "user_id": ALICE, "pool": self.pool}}
        result = await graph.ainvoke({"messages": [HumanMessage(content="hello", id="user")],
                                     "video_frames": None, "use_coding_agent": False}, config)
        self.assertEqual(result["messages"][-1].content, "done")
        self.assertEqual(len(model.seen), 2)
        for request in model.seen:
            self.assertEqual(sum(isinstance(m, SystemMessage) for m in request), 1)
            self.assertEqual(request[0].content, "dynamic")
        archived = await load_messages(self.pool, "alice-thread")
        self.assertTrue(any(m.type == "tool" for m in archived))
        self.assertEqual(archived[-1].content, "done")

    async def test_summary_is_created_before_inference_and_history_is_not_lost(self):
        model = CaptureModel()
        summariser = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="Earlier decisions")))
        agent = create_agent(model, tools=[echo], name="main_agent",
                             middleware=[context_middleware(lambda memory, summary: "dynamic" + summary)])
        graph = build_graph(agent, agent, agent, InMemorySaver())
        config = {"configurable": {"thread_id": "alice-thread", "user_id": ALICE,
                                    "pool": self.pool, "summariser_llm": summariser}}
        with patch("llm.context.CONTEXT_BUDGET", 3000):
            result = await graph.ainvoke({"messages": [HumanMessage(content="x" * 15_000, id="old"),
                                              AIMessage(content="old answer", id="old-ai"),
                                              HumanMessage(content="hello", id="new")],
                                 "video_frames": None, "use_coding_agent": False}, config)
        summariser.ainvoke.assert_awaited_once()
        self.assertIn("Earlier decisions", model.seen[0][0].content)
        self.assertFalse(any(m.id == "old" for m in model.seen[0]))
        self.assertFalse(any(m.id == "old" for m in result["messages"]))
        self.assertTrue(any(m.id == "old" for m in await load_messages(self.pool, "alice-thread")))

    async def test_completed_stream_uses_durable_output_and_checks_ownership(self):
        self.pool.jobs["saved"] = ("done", "persisted answer", None, ALICE)
        response = await self.client.get("/chat/stream/saved")
        self.assertIn('"content": "persisted answer"', response.text)
        self.assertIn('"done": true', response.text)
        self.pool.jobs["saved"] = ("done", "bob secret", None, BOB)
        self.assertEqual((await self.client.get("/chat/stream/saved")).status_code, 404)

    async def test_personal_memory_reaches_all_three_agent_factories(self):
        for factory in (create_agents.init_main_agent, create_agents.init_coding_agent, create_agents.init_video_agent):
            model = CaptureModel(respond_with_tools=False)
            with patch.object(create_agents, "LLM", return_value=SimpleNamespace(model=model)):
                agent = factory()
            graph = build_graph(agent, agent, agent, InMemorySaver())
            config = {"configurable": {"thread_id": "alice-thread", "user_id": ALICE, "pool": self.pool}}
            with patch("llm.context.load_user_memory", AsyncMock(return_value="Preferred language: French")) as memory:
                await graph.ainvoke({"messages": [HumanMessage(content="hello", id="user")],
                                     "video_frames": None, "use_coding_agent": False}, config)
            self.assertIn("Preferred language: French", model.seen[0][0].content, factory.__name__)
            memory.assert_awaited_once_with(self.pool, ALICE)

    async def test_ui_files_are_revalidated_on_reload(self):
        for path in ("/", "/static/app.js"):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-cache")

    async def test_worker_output_and_reloaded_history_contain_the_complete_answer(self):
        previous = [HumanMessage(content="old question", id="old-user"),
                    AIMessage(content="old answer", id="old-ai")]
        async def invoke(inputs, config):
            return {"messages": previous + inputs["messages"] + [
                AIMessage(content="Option 1", id="first-part",
                          tool_calls=[{"name": "write_file_tool", "args": {
                              "filename": "wake_up.ps1", "content": 'Write-Host "Hello"\n'}, "id": "call"}]),
                ToolMessage(content="Wrote 'wake_up.ps1' (19 chars).", tool_call_id="call", id="tool-result"),
                AIMessage(content="Option 2", id="last-part"),
            ]}
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={"messages": previous})),
                                ainvoke=invoke)
        output = await main.run_agent_task(
            app_state=SimpleNamespace(graph=graph, pool=self.pool, summariser_llm=None),
            task_id="current-task", thread_id="alice-thread", user_id=ALICE, message="question",
        )
        self.assertIn('```powershell\nWrite-Host "Hello"\n```', output)
        self.assertTrue(output.startswith('Option 1'))
        self.assertTrue(output.endswith('Option 2'))
        response = await self.client.get("/conversations/alice-thread/messages")
        answers = [m["content"] for m in response.json() if m["role"] == "assistant"]
        self.assertEqual(answers, ["old answer", output])
        archived = await load_messages(self.pool, "alice-thread")
        self.assertEqual(sum(m.type == "tool" for m in archived), 1)


class ValidationTests(unittest.TestCase):
    def test_limits_and_remote_image_urls_are_enforced(self):
        for extra in [{"message": "x" * 60_001}, {"images": ["https://internal/secret"]},
                      {"video_frames": ["data:image/jpeg;base64,AA=="] * 17}]:
            with self.assertRaises(ValidationError):
                main.ChatRequest(**({"message": "hello", "thread_id": "thread"} | extra))


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_persists_timeout_and_cancels_execution(self):
        finished = asyncio.Event()
        cancelled = asyncio.Event()
        async def execute(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async def finish(*args, **kwargs):
            self.assertIn("time limit", kwargs["error"])
            finished.set()
        with patch.object(tasks, "claim", AsyncMock(side_effect=[("job", "thread", ALICE, {}), None])), \
             patch.object(tasks, "recover_stale", AsyncMock()), \
             patch.object(tasks, "finish", side_effect=finish), patch.object(tasks, "TASK_TIMEOUT", 0.01):
            worker = asyncio.create_task(tasks.worker_loop(SimpleNamespace(pool=object()), execute))
            await asyncio.wait_for(finished.wait(), 2)
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        self.assertTrue(cancelled.is_set())

    async def test_shutdown_marks_running_task_interrupted(self):
        started = asyncio.Event()
        async def execute(**kwargs):
            started.set()
            await asyncio.Event().wait()
        with patch.object(tasks, "claim", AsyncMock(return_value=("job", "thread", ALICE, {}))), \
             patch.object(tasks, "recover_stale", AsyncMock()), patch.object(tasks, "finish", AsyncMock()) as finish:
            worker = asyncio.create_task(tasks.worker_loop(SimpleNamespace(pool=object()), execute))
            await asyncio.wait_for(started.wait(), 2)
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            self.assertIn("interrupted", finish.call_args.kwargs["error"])


if __name__ == "__main__":
    unittest.main()
