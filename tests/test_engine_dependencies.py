"""Tests for engine.dependencies — task execution order constraints."""

import pytest

from engine.dependencies import DependencyManager
from engine.store import StoreManager
from engine.models import Task, TaskStatus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    deps = DependencyManager(store)
    yield deps, store
    store.close()


class TestDependencies:
    def test_no_deps_satisfied(self, components):
        deps, store = components
        t = Task(agent_id="claude", title="no deps")
        store.insert_task(t)
        assert deps.are_deps_satisfied(t.id)

    def test_add_dependency(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="first")
        t2 = Task(agent_id="claude", title="second")
        store.insert_task(t1)
        store.insert_task(t2)

        deps.add_dependency(t2.id, t1.id)
        dep_list = deps.get_dependencies(t2.id)
        assert len(dep_list) == 1
        assert dep_list[0].depends_on == t1.id

    def test_unsatisfied_dep(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="first")
        t2 = Task(agent_id="claude", title="second")
        store.insert_task(t1)
        store.insert_task(t2)

        deps.add_dependency(t2.id, t1.id)
        assert not deps.are_deps_satisfied(t2.id)

    def test_satisfied_after_complete(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="first")
        t2 = Task(agent_id="claude", title="second")
        store.insert_task(t1)
        store.insert_task(t2)

        deps.add_dependency(t2.id, t1.id)
        store.update_task_status(t1.id, TaskStatus.COMPLETED)
        assert deps.are_deps_satisfied(t2.id)

    def test_on_task_completed_returns_ready(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="first")
        t2 = Task(agent_id="claude", title="second")
        store.insert_task(t1)
        store.insert_task(t2)

        deps.add_dependency(t2.id, t1.id)
        store.update_task_status(t1.id, TaskStatus.COMPLETED)

        ready = deps.on_task_completed(t1.id)
        assert t2.id in ready

    def test_multiple_deps_all_needed(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="dep-1")
        t2 = Task(agent_id="claude", title="dep-2")
        t3 = Task(agent_id="claude", title="main")
        store.insert_task(t1)
        store.insert_task(t2)
        store.insert_task(t3)

        deps.add_dependency(t3.id, t1.id)
        deps.add_dependency(t3.id, t2.id)

        # Only t1 complete — not ready
        store.update_task_status(t1.id, TaskStatus.COMPLETED)
        assert not deps.are_deps_satisfied(t3.id)
        ready = deps.on_task_completed(t1.id)
        assert t3.id not in ready

        # Both complete — now ready
        store.update_task_status(t2.id, TaskStatus.COMPLETED)
        ready = deps.on_task_completed(t2.id)
        assert t3.id in ready

    def test_on_task_failed(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="will fail")
        t2 = Task(agent_id="claude", title="depends on failed")
        store.insert_task(t1)
        store.insert_task(t2)

        deps.add_dependency(t2.id, t1.id)
        blocked = deps.on_task_failed(t1.id)
        assert t2.id in blocked

    def test_get_dependents(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="parent")
        t2 = Task(agent_id="claude", title="child-1")
        t3 = Task(agent_id="claude", title="child-2")
        store.insert_task(t1)
        store.insert_task(t2)
        store.insert_task(t3)

        deps.add_dependency(t2.id, t1.id)
        deps.add_dependency(t3.id, t1.id)

        dependents = deps.get_dependents(t1.id)
        assert set(dependents) == {t2.id, t3.id}

    def test_chain_dependencies(self, components):
        """A → B → C chain."""
        deps, store = components
        a = Task(agent_id="claude", title="A")
        b = Task(agent_id="claude", title="B")
        c = Task(agent_id="claude", title="C")
        store.insert_task(a)
        store.insert_task(b)
        store.insert_task(c)

        deps.add_dependency(b.id, a.id)
        deps.add_dependency(c.id, b.id)

        # Nothing ready initially
        assert not deps.are_deps_satisfied(b.id)
        assert not deps.are_deps_satisfied(c.id)

        # Complete A → B ready, C not
        store.update_task_status(a.id, TaskStatus.COMPLETED)
        ready = deps.on_task_completed(a.id)
        assert b.id in ready
        assert c.id not in ready

        # Complete B → C ready
        store.update_task_status(b.id, TaskStatus.COMPLETED)
        ready = deps.on_task_completed(b.id)
        assert c.id in ready

    def test_duplicate_dep_ignored(self, components):
        deps, store = components
        t1 = Task(agent_id="claude", title="a")
        t2 = Task(agent_id="claude", title="b")
        store.insert_task(t1)
        store.insert_task(t2)

        deps.add_dependency(t2.id, t1.id)
        deps.add_dependency(t2.id, t1.id)  # duplicate
        dep_list = deps.get_dependencies(t2.id)
        assert len(dep_list) == 1
