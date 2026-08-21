"""T015: the graph's checkpointer -- sync ``SqliteSaver``, bridged for async use.

research.md's "Failure handling" decision and spec.md US4 pin the
checkpointer to **``SqliteSaver``** on
``langgraph-checkpoint-sqlite>=3.0.1`` (the CVE-2025-67644 floor) -- not the
async ``AsyncSqliteSaver`` variant, which pulls in a separate ``aiosqlite``
dependency this project never adopted. That choice collides with one fact
about :mod:`katagiri_agent.graph`: every node there is an ``async def`` that
``await``s ``tool.ainvoke(...)`` (T013's own design, see that module's
docstring), so the compiled graph can only be driven through
``.ainvoke()``/``.astream()`` -- LangGraph raises ``TypeError`` ("No
synchronous function provided") the moment ``.invoke()`` is tried against an
async node.

``SqliteSaver`` itself does not support that: its ``aget_tuple`` / ``alist`` /
``aput`` overrides raise ``NotImplementedError`` unconditionally, telling the
caller to use ``AsyncSqliteSaver`` instead (verified against the installed
``langgraph-checkpoint-sqlite==3.1.1`` source). :class:`AsyncBridgeSqliteSaver`
below is the smallest fix that keeps the pin: it is a plain ``SqliteSaver``
subclass whose async checkpoint methods do nothing but hand the *already
thread-safe* sync method (every sync method serialises through
``SqliteSaver``'s own ``self.lock`` inside ``cursor()``) to the default
executor. Storage format, locking, and WAL mode are all exactly
``SqliteSaver``'s own -- this file adds an async *calling convention*, not a
different persistence layer, which is what keeps the CVE-floor pin meaningful
rather than a pin on a class nothing actually uses.
"""

from __future__ import annotations

import asyncio
import functools
import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.sqlite import SqliteSaver


class AsyncBridgeSqliteSaver(SqliteSaver):
    """``SqliteSaver`` + the minimal async bridge :func:`build_graph` needs.

    Every method here is a direct ``run_in_executor`` call to the matching
    sync method already defined on :class:`SqliteSaver` -- no new SQL, no
    new locking. It exists only so the T013 graph (async nodes, no sync
    entry point) can be driven with a real ``SqliteSaver`` underneath it,
    per research.md's pin.
    """

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.get_tuple, config))

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(
            None,
            functools.partial(
                lambda: list(self.list(config, filter=filter, before=before, limit=limit))
            ),
        )
        for item in items:
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.put, config, checkpoint, metadata, new_versions),
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            functools.partial(self.put_writes, config, writes, task_id, task_path),
        )

    async def adelete_thread(self, thread_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, functools.partial(self.delete_thread, thread_id))


@contextmanager
def open_checkpointer(db_path: str | Path) -> Iterator[AsyncBridgeSqliteSaver]:
    """Open a **file-backed** checkpointer at ``db_path``.

    ``check_same_thread=False`` is required and safe: :class:`SqliteSaver`
    serialises every access (sync or bridged-async) through its own
    ``threading.Lock``, so handing the connection to whichever worker thread
    ``run_in_executor`` happens to pick is exactly the documented,
    supported usage (see the ``SqliteSaver`` class docstring's own example).

    Parent directories are created if missing -- this is meant to be pointed
    at a fresh ``tmp_path`` in tests and at a real checkpoint file in a demo
    run, neither of which is guaranteed to exist yet.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield AsyncBridgeSqliteSaver(conn)
    finally:
        conn.close()


def thread_config(thread_id: str, *, checkpoint_ns: str = "") -> RunnableConfig:
    """The ``config`` dict every checkpointed run/resume call needs.

    One ``thread_id`` == one resumable flow. Callers resume a killed run by
    re-using the exact same ``thread_id`` against the same checkpoint file --
    nothing else identifies "which run" to LangGraph.
    """
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}


__all__ = [
    "AsyncBridgeSqliteSaver",
    "open_checkpointer",
    "thread_config",
]
