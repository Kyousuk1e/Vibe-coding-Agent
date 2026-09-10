"""Compose production modules; tests inject a deterministic model client."""

from dataclasses import dataclass

from .context import ContextManager
from .llm import ChatClient
from .runtime import AgentRuntime
from .store import SessionStore
from .tools import create_tools
from .trace import TraceWriter


@dataclass
class App:
    store: SessionStore
    runtime: AgentRuntime
    registry: object


def create_app(config, *, store=None, client=None, registry=None, context=None, trace_writer=None):
    store = store if store is not None else SessionStore(config.data_dir)
    registry = registry if registry is not None else create_tools(max_result_chars=16000)
    client = client if client is not None else ChatClient(config)
    context = context if context is not None else ContextManager(max_context_chars=config.max_context_chars)
    trace_writer = trace_writer if trace_writer is not None else TraceWriter(config.data_dir)
    runtime = AgentRuntime(store=store, client=client, registry=registry, context=context,
                           trace_writer=trace_writer, max_steps=config.max_steps)
    return App(store, runtime, registry)
