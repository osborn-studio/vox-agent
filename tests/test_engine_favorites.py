"""Tests for engine.favorites — task bookmarks."""

import pytest
from engine.favorites import FavoritesManager
from engine.store import StoreManager
from engine.models import Task


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    fav = FavoritesManager(store)
    yield fav, store
    store.close()


class TestFavorites:
    def test_toggle(self, components):
        fav, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        assert fav.toggle(t.id) is True
        assert fav.is_favorite(t.id)
        assert fav.toggle(t.id) is False
        assert not fav.is_favorite(t.id)

    def test_set(self, components):
        fav, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        fav.set(t.id, True)
        assert fav.is_favorite(t.id)
        fav.set(t.id, False)
        assert not fav.is_favorite(t.id)

    def test_list_favorites(self, components):
        fav, store = components
        t1 = Task(agent_id="claude", title="fav")
        t2 = Task(agent_id="claude", title="not fav")
        store.insert_task(t1)
        store.insert_task(t2)
        fav.set(t1.id, True)
        favs = fav.list_favorites()
        assert len(favs) == 1
        assert favs[0]["id"] == t1.id

    def test_count(self, components):
        fav, store = components
        assert fav.count() == 0
        for i in range(3):
            t = Task(agent_id="claude", title=f"t{i}")
            store.insert_task(t)
            fav.set(t.id, True)
        assert fav.count() == 3

    def test_nonexistent(self, components):
        fav, _ = components
        assert not fav.is_favorite("nope")
        assert fav.toggle("nope") is False

    def test_default_not_favorite(self, components):
        fav, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        assert not fav.is_favorite(t.id)
