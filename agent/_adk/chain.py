"""DeterministicChain — ADK BaseAgent that runs pipeline stages with NO LLM.

Direct descendant of v9's pattern: an LlmAgent per pipeline step costs two
pointless model round-trips per step. Here ADK provides sessions, state and
the web UI; the chain provides sequencing. Each stage is a pure async
function `stage(state: dict, llm) -> {ok, message, delta}`; deltas are
propagated through EventActions.state_delta so they persist in the session.

Intake is handled here (not in a stage) because it needs the InvocationContext:
the user message is either a file path or an attached document.
"""
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Callable

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types

from app.llm import get_llm


class DeterministicChain(BaseAgent):
    model_config = {"arbitrary_types_allowed": True}
    steps: list[tuple[str, Callable]] = []

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        state: dict[str, Any] = dict(ctx.session.state or {})

        intake_delta, err = self._intake(ctx)
        if err:
            yield self._event(ctx, f"Cannot process this input: {err}", {})
            return
        state.update(intake_delta)
        yield self._event(ctx, f"Input received: {state.get('input_name', '?')}", intake_delta)

        llm = get_llm()
        for name, fn in self.steps:
            try:
                result = await fn(state, llm)
            except Exception as exc:  # a stage crash must surface, never hang the chain
                yield self._event(ctx, f"[{name}] failed: {exc!r}", {})
                return
            delta = result.get("delta", {})
            state.update(delta)
            yield self._event(ctx, f"[{name}] {result.get('message', 'done')}", delta)
            if not result.get("ok", True):
                return

    def _intake(self, ctx):
        """User message → input_path in state. Path string or attached bytes."""
        content = getattr(ctx, "user_content", None)
        text_parts, blob = [], None
        for part in (content.parts if content and content.parts else []):
            if getattr(part, "text", None):
                text_parts.append(part.text.strip())
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                blob = data
        text = " ".join(t for t in text_parts if t).strip().strip('"')

        if blob:
            f = tempfile.NamedTemporaryFile(prefix="anz_", suffix=".bin", delete=False)
            f.write(blob)
            f.close()
            return {"input_path": f.name, "input_name": "attachment"}, None
        if text and Path(text).is_file():
            return {"input_path": text, "input_name": Path(text).name}, None
        return {}, (f"no attachment and no existing file at {text!r} — "
                    "send a .docx/document.xml path or attach the file")

    def _event(self, ctx, message: str, delta: dict) -> Event:
        return Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=types.Content(role="model", parts=[types.Part(text=message)]),
            actions=EventActions(state_delta=delta or {}),
        )
