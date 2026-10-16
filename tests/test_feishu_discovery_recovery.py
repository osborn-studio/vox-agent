"""Cross-user failure recovery and valid metadata, entirely offline."""
import pytest

from engine.backup_app import ConversationPolicy
from engine.feishu_client import DiscoveryError
from engine.feishu_conversations import ConversationDiscovery, ConversationMappings


class Directory:
    app_id = "cli_test"

    def __init__(self, tenant="team", name="Engineering"):
        self.tenant, self.name = tenant, name
        self.fail = False

    def chats(self, **kwargs):
        if self.fail:
            raise DiscoveryError("UPSTREAM_UNAVAILABLE")
        return [{"chat_id": "oc_group", "name": self.name,
                 "tenant_key": self.tenant, "external": False}]

    def members(self, *args, **kwargs):
        return [{"member_id": "ou_alice"}, {"member_id": "ou_bob"}]


def identity(user="alice", tenant="team"):
    return {"app_id": "cli_test", "tenant": tenant, "user_id": user, "open_id": "ou_" + user}


def test_new_principal_during_cooldown_does_not_poison_recovery(tmp_path):
    now = [0]
    directory = Directory()
    directory.fail = True
    mappings = ConversationMappings(tmp_path / "mapping.sqlite")
    try:
        service = ConversationDiscovery(directory, mappings, clock=lambda: now[0])
        with pytest.raises(DiscoveryError, match="UPSTREAM_UNAVAILABLE"):
            service.list(identity(), ConversationPolicy())
        now[0] = 1
        with pytest.raises(DiscoveryError, match="RATE_LIMITED"):
            service.list(identity("bob"), ConversationPolicy())
        now[0] = 10
        directory.fail = False
        assert len(service.list(identity("bob"), ConversationPolicy())) == 2
    finally:
        mappings.close()


@pytest.mark.parametrize("tenant,name", [("members", "Engineering"), ("team", "Developers 👩‍💻")])
def test_valid_tenant_key_and_emoji_group_are_discoverable(tmp_path, tenant, name):
    mappings = ConversationMappings(tmp_path / "mapping.sqlite")
    try:
        service = ConversationDiscovery(Directory(tenant, name), mappings)
        assert service.list(identity(tenant=tenant), ConversationPolicy())[1]["name"] == name
    finally:
        mappings.close()


def test_malformed_chat_is_refetched_after_cooldown(tmp_path):
    now = [0]
    directory = Directory(name=None)
    mappings = ConversationMappings(tmp_path / "mapping.sqlite")
    try:
        service = ConversationDiscovery(directory, mappings, clock=lambda: now[0])
        with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
            service.list(identity(), ConversationPolicy())
        directory.name = "Fixed"
        now[0] = 6
        assert service.list(identity(), ConversationPolicy())[1]["name"] == "Fixed"
    finally:
        mappings.close()


def test_display_controls_are_neutralized_without_hiding_groups(tmp_path):
    mappings = ConversationMappings(tmp_path / "mapping.sqlite")
    try:
        service = ConversationDiscovery(Directory(name="A\u202eB\nC 👩‍💻"), mappings)
        rows = service.list(identity(), ConversationPolicy())
        assert len(rows) == 2
        assert rows[1]["name"] == "A\ufffdB\ufffdC 👩‍💻"
        # Nonmembers still get their personal channel; one group's name cannot
        # poison discovery for the rest of the organization.
        assert len(service.list(identity("carol"), ConversationPolicy())) == 1
    finally:
        mappings.close()


@pytest.mark.parametrize("name,expected", [
    ("", "Unnamed group"), ("x" * 300, "x" * 256), ("A\ud800B", "A\ufffdB"),
])
def test_display_names_cannot_deny_members_or_nonmembers(tmp_path, name, expected):
    mappings = ConversationMappings(tmp_path / "mapping.sqlite")
    try:
        service = ConversationDiscovery(Directory(name=name), mappings)
        assert len(service.list(identity("carol"), ConversationPolicy())) == 1
        rows = service.list(identity(), ConversationPolicy())
        assert len(rows) == 2
        assert rows[1]["name"] == expected
        rows[1]["name"].encode("utf-8")
    finally:
        mappings.close()


def test_repeated_chat_id_is_rendered_once_with_membership_still_required(tmp_path):
    class RepeatedDirectory(Directory):
        def chats(self, **kwargs):
            return super().chats(**kwargs) * 2

    mappings = ConversationMappings(tmp_path / "mapping.sqlite")
    try:
        service = ConversationDiscovery(RepeatedDirectory(), mappings)
        assert len(service.list(identity("carol"), ConversationPolicy())) == 1
        assert len(service.list(identity(), ConversationPolicy())) == 2
    finally:
        mappings.close()


@pytest.mark.parametrize("foreign", [{"external": True}, {"tenant_key": "other"}])
@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_duplicate_eligibility_fails_closed(tmp_path, foreign, reverse):
    class ConflictingDirectory(Directory):
        def chats(self, **kwargs):
            eligible = super().chats(**kwargs)[0]
            rows = [eligible, {**eligible, **foreign}]
            return rows[::-1] if reverse else rows

    mappings = ConversationMappings(tmp_path / "mapping.sqlite")
    try:
        service = ConversationDiscovery(ConflictingDirectory(), mappings)
        with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
            service.list(identity(), ConversationPolicy())
        assert not service.cache
    finally:
        mappings.close()
