"""Translation adds no fetching rights; exercise the real authorized reading path."""

import pytest
from pydantic import ValidationError

from engine.backup_app import SharedReadPolicy
from engine.feishu_conversation_reading import ReadingError
from tests.test_feishu_conversation_reading import env as reading_environment, share


@pytest.fixture
def env(tmp_path):
    yield from reading_environment.__wrapped__(tmp_path)


def test_default_denies_other_actor(env):
    share(env)
    page = env.read()
    assert page["translationCapability"] == "explicit_per_item"
    assert page["items"][0]["modelTranslationAllowed"] is False
    assert len([r for r in env.calls if r.url.path.endswith("/messages")]) == 1


def test_explicit_policy_and_revision(env):
    share(env)
    before = env.read()
    share(env, allow_model_translation=True)
    after = env.read()
    assert after["items"][0]["modelTranslationAllowed"] is True
    assert before["policyRevision"] != after["policyRevision"]
    share(env)
    assert env.read()["items"][0]["modelTranslationAllowed"] is False


def test_own_sender_allowed_without_model_policy(env):
    share(env)
    env.items[0]["sender"]["id"] = env.identity["open_id"]
    assert env.read()["items"][0]["modelTranslationAllowed"] is True


@pytest.mark.parametrize("flag", ["deleted", "placeholder"])
def test_inert_items_never_allowed(env, flag):
    share(env, allow_model_translation=True)
    if flag == "deleted":
        env.items[0]["deleted"] = True
    else:
        env.items[0]["msg_type"] = "post"
    assert env.read()["items"][0]["modelTranslationAllowed"] is False


def test_policy_change_during_read_rejects(env):
    share(env)
    env.hook = lambda: share(env, allow_model_translation=True)
    with pytest.raises(ReadingError, match="AUTHORIZATION_RESET"):
        env.read()


@pytest.mark.parametrize("value", [1, "true", None])
def test_policy_strict_boolean(env, value):
    share(env)
    values = env.ctx.conversation_policy.shared_read_chat_ids[0].model_dump()
    values["allow_model_translation"] = value
    with pytest.raises(ValidationError):
        SharedReadPolicy(**values)


def test_own_original_without_sharing_has_no_history_fetch(env):
    from tests.test_feishu_conversation_reading import original

    original(env)
    result = env.read()
    assert result["mode"] == "own_submissions"
    assert result["items"][0]["modelTranslationAllowed"] is True
    assert not any(r.url.path.endswith("/messages") for r in env.calls)


def test_bound_bot_dm_and_cross_account(env):
    from tests.test_feishu_conversation_reading import original

    original(env, target="dm", conversation=env.dm)
    env.store.complete(env.store.next_job(), {"message_id": "om_one", "chat_id": "oc_dm"})
    env.items[0]["chat_id"] = "oc_dm"
    env.items[0]["sender"] = {
        "id": env.identity["app_id"],
        "id_type": "app_id",
        "sender_type": "app",
    }
    assert env.read(env.dm)["items"][0]["modelTranslationAllowed"] is True
    env.identity = {**env.identity, "user_id": "bob"}
    with pytest.raises(ReadingError, match="CONVERSATION_DENIED"):
        env.read(env.dm)


def test_saved_other_actor_is_not_own_even_with_own_app_sender(env):
    from engine.feishu_conversation_store import SendInput

    share(env)
    other = {**env.identity, "user_id": "bob", "open_id": "ou_bob", "name": "Bob"}
    env.store.accept(other, "fc_other", "oc_group", SendInput(eventId="other", text="original"))
    env.store.complete(env.store.next_job(), {"message_id": "om_one", "chat_id": "oc_group"})
    env.items[0]["sender"] = {
        "id": env.identity["app_id"],
        "id_type": "app_id",
        "sender_type": "app",
    }
    item = env.read()["items"][0]
    assert item["authorId"] == "bob"
    assert item["modelTranslationAllowed"] is False


def test_explicit_model_policy_never_expands_cutoff_or_quote(env):
    import json

    share(env, allow_model_translation=True)
    env.items[0]["create_time"] = "999999"
    assert env.read()["items"] == []
    env.items[0]["create_time"] = "1000000"
    env.items[0]["body"]["content"] = json.dumps({"text": "quote", "quote": {"text": "private"}})
    item = env.read()["items"][0]
    assert item["text"] is None and item["modelTranslationAllowed"] is False
