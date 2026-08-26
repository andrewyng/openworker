"""todo_write's wire contract.

The parameter is `todos` — a top-level arguments key named "items" shadows minijinja's
`.items()` map method in hosted chat templates (Together GLM-5.2, 2026-07-21) and 400s
every request that replays the call. The old key stays accepted at execution time for
models that free-style it, but must never reappear in the schema.
"""

from coworker.tools.todo import _TODO_SCHEMA, TodoList, todo_tools


def _write(**kwargs):
    todo = TodoList()
    (spec,) = todo_tools(todo)
    return spec(**kwargs), todo


def test_schema_param_is_todos_not_items():
    props = _TODO_SCHEMA["function"]["parameters"]["properties"]
    assert "todos" in props
    assert "items" not in props  # regression guard: see module docstring
    assert _TODO_SCHEMA["function"]["parameters"]["required"] == ["todos"]


def test_todos_key_writes_the_list():
    result, todo = _write(todos=[{"content": "a", "status": "in_progress"}])
    assert todo.items == [{"content": "a", "status": "in_progress"}]
    assert result == {"count": 1, "todos": [{"content": "a", "status": "in_progress"}]}


def test_legacy_items_key_still_executes():
    result, todo = _write(items=[{"content": "b", "status": "done"}])
    assert todo.items == [{"content": "b", "status": "done"}]
    assert result["count"] == 1


# -- plan staleness ---------------------------------------------------------------
#
# The list is the ONLY thing the Progress panel can render, and nothing in the loop ever asked
# for it to be rewritten. A measured run: a five-item plan written at call 0 of a 101-call turn,
# revised once at call 24, then 76 calls that finished items 2-5 and committed them without a
# word. The panel showed "item 2 in progress, 3-5 pending" for the rest of the run and after it
# ended — faithful to the last call, and 76 steps behind the truth.

from coworker.tools.todo import _STALE_AFTER, stale_plan_notice


def _plan(*statuses):
    return TodoList(items=[{"content": f"item {i}", "status": s} for i, s in enumerate(statuses)])


def _calls(n, name="read_file"):
    """`n` assistant messages, one tool call each — the run moving on."""
    return [{"role": "assistant", "tool_calls": [{"function": {"name": name}}]} for _ in range(n)]


def _wrote_the_plan():
    return {"role": "assistant", "tool_calls": [{"function": {"name": "todo_write"}}]}


def test_a_plan_the_run_has_left_behind_is_flagged():
    notice = stale_plan_notice(
        _plan("done", "in_progress", "pending"),
        [_wrote_the_plan(), *_calls(_STALE_AFTER)],
    )
    assert "todo_write" in notice
    assert "out of date" in notice


def test_a_plan_written_a_moment_ago_is_left_alone():
    assert (
        stale_plan_notice(
            _plan("done", "in_progress", "pending"),
            [_wrote_the_plan(), *_calls(_STALE_AFTER - 1)],
        )
        == ""
    )


def test_the_most_recent_write_is_what_ages_the_plan():
    # The run revises the list mid-flight; the clock restarts there, not at the first write.
    messages = [_wrote_the_plan(), *_calls(50), _wrote_the_plan(), *_calls(2)]
    assert stale_plan_notice(_plan("done", "in_progress"), messages) == ""


def test_a_finished_list_is_not_a_stale_one():
    assert stale_plan_notice(_plan("done", "done"), [_wrote_the_plan(), *_calls(50)]) == ""


def test_no_plan_means_nothing_to_nag_about():
    # A persona without the todo tool, or a run that never planned: the notice would be
    # instructing the model to use a tool it may not even have.
    assert stale_plan_notice(TodoList(), _calls(50)) == ""
    assert stale_plan_notice(None, _calls(50)) == ""


def test_a_plan_whose_write_fell_out_of_history_still_ages():
    # Compaction dropped the todo_write, or the thread was resumed in a new process. The list
    # came from somewhere, so everything visible counts against it.
    assert stale_plan_notice(_plan("in_progress"), _calls(_STALE_AFTER)) != ""


def test_parallel_calls_in_one_message_each_count_as_a_step():
    burst = [{"role": "assistant", "tool_calls": [{"function": {"name": "grep"}}] * _STALE_AFTER}]
    assert stale_plan_notice(_plan("in_progress"), [_wrote_the_plan(), *burst]) != ""


def test_the_writes_own_batch_does_not_age_it():
    # A model that plans and then works in ONE parallel batch emits todo_write alongside the
    # calls it is about to make. Those siblings are not steps the list fell behind by: counting
    # them told the model, on the very next round trip, that the list it had just written was
    # stale.
    together = {
        "role": "assistant",
        "tool_calls": [{"function": {"name": "todo_write"}}]
        + [{"function": {"name": "read_file"}}] * (_STALE_AFTER + 4),
    }
    assert stale_plan_notice(_plan("in_progress"), [together]) == ""
    # ...and the batch after it still ages it normally.
    assert stale_plan_notice(_plan("in_progress"), [together, *_calls(_STALE_AFTER)]) != ""


def test_build_engine_nags_only_once_the_run_has_moved_on(tmp_path):
    # The wiring: the notice reaches the model through the ephemeral per-turn context block, so
    # it is never persisted, never replayed into the transcript, and clears itself the round
    # trip after the list is rewritten.
    from coworker.agent import build_engine
    from coworker.agents import cowork_agent
    from coworker.providers.base import ModelCapabilities

    class _Stub:
        def complete(self, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

    engine = build_engine(agent=cowork_agent(), workspace=tmp_path, provider=_Stub())
    try:
        assert "out of date" not in engine.context_provider()  # no plan yet

        (todo_write,) = [t for t in engine.registry.names() if t == "todo_write"]
        engine.registry.execute(
            todo_write, {"todos": [{"content": "extend preflight", "status": "in_progress"}]}
        )
        engine.messages.extend([_wrote_the_plan(), *_calls(_STALE_AFTER)])
        assert "out of date" in engine.context_provider()

        # Rewriting it clears the notice — nothing accumulates and nothing persists.
        engine.registry.execute(
            todo_write, {"todos": [{"content": "extend preflight", "status": "done"}]}
        )
        engine.messages.append(_wrote_the_plan())
        assert "out of date" not in engine.context_provider()
        assert not any("out of date" in str(m.get("content", "")) for m in engine.messages)
    finally:
        engine.executor.close()
