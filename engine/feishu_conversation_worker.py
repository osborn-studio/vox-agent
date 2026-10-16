"""Single-process delivery owner. No audit/log retains text or upstream exceptions."""

import fcntl
import threading

from .feishu_conversation_store import SendingError, private_file


class ConversationWorker:
    def __init__(self, store, client, *, authorize):
        self.store, self.client, self.authorize = store, client, authorize
        self.guard = threading.Lock()
        self.handle = None

    def __enter__(self):
        path = private_file(str(self.store.path) + ".lock")
        handle = open(path, "r+")
        failed = False
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            failed = True
        if failed:
            handle.close()
            raise SendingError("WORKER_ALREADY_RUNNING")
        self.handle = handle
        self.store.recover()
        return self

    def __exit__(self, *args):
        if self.handle:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

    def run_once(self):
        if self.handle is None:
            raise RuntimeError("WORKER_NOT_STARTED")
        with self.guard:
            job = self.store.next_job()
            if job is None:
                return
            permitted, unavailable = False, False
            try:
                permitted = self.authorize(job)
            except Exception:
                unavailable = True
            if unavailable:
                self.store.update(job, "pending", "PREFLIGHT_UNAVAILABLE")
                return
            if not permitted:
                self.store.update(job, "membership_lost", "AUTHORIZATION_LOST")
                return
            from .feishu_client import FeishuClient, SendFailure

            response, failure = None, None
            try:
                args = (job["open_id"] if job["target"] == "dm" else job["target"],
                        job["rendered"], job["delivery_uuid"])
                if isinstance(self.client, FeishuClient):
                    response = self.client.send_text(*args, before_wire=lambda: self.store.begin(job))
                elif self.store.begin(job):
                    response = self.client.send_text(*args)
                else:
                    return
            except SendFailure as error:
                failure = error.kind
            except Exception:
                failure = "uncertain"
            if response is None:
                state, reason = {
                    "membership": ("membership_lost", "AUTHORIZATION_LOST"),
                    "failed": ("failed", "SEND_REJECTED"),
                    "backoff": ("pending", "SEND_BACKOFF"),
                    "no_wire": ("pending", "LOCAL_BACKOFF"),
                    "retired": ("unknown", "RETRY_RETIRED"),
                }.get(failure, ("unknown", "DELIVERY_UNCERTAIN"))
                if failure != "retired":
                    self.store.update(job, state, reason)
            else:
                self.store.complete(job, response)


def authorize_current(context, job):
    """Fresh complete eligibility/member traversal, never a display-cache lookup."""
    from .auth import Principal
    from .feishu_client import DiscoveryError
    from .feishu_conversation_store import scope
    import re

    if not context.conversations_enabled:
        raise DiscoveryError("UPSTREAM_UNAVAILABLE")
    login = context.auth_service.feishu_login
    principal = Principal(job["user_id"], job["org_id"], "member", "feishu")
    current = login.conversation_identity(principal)
    client = context.conversation_discovery.client

    def matches(identity):
        return (
            identity is not None
            and scope(identity) == scope(job)
            and identity["open_id"] == job["open_id"]
            and (identity["app_id"], identity["tenant"]) == (client.app_id, client.tenant)
            and context.conversation_discovery.mappings.resolve(identity, job["conversation"])
            == job["target"]
        )

    if not matches(current):
        return False
    if job["target"] != "dm":
        if job["target"] in context.conversation_policy.denied_chat_ids:
            return False
        deadline = client.clock() + 8
        chats = context.conversation_discovery._validated_chats(client.chats(deadline=deadline), strict_eligibility=True)
        if not any(c["chat_id"] == job["target"] for c in chats):
            return False
        members = client.members(job["target"], deadline=deadline)
        if any(
            not isinstance(m.get("member_id"), str)
            or not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,253}", m["member_id"])
            for m in members
        ):
            raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
        if not any(m["member_id"] == current["open_id"] for m in members):
            return False
    # Canonical identity/policy recheck after network traversal; still an unavoidable
    # in-flight policy/membership race between this point and the upstream send.
    return (
        context.conversations_enabled
        and matches(login.conversation_identity(principal))
        and job["target"] not in context.conversation_policy.denied_chat_ids
    )


class ConversationRuntime:
    def __init__(self):
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.failed = threading.Event()
        self.thread = None

    def start(self, context):
        def run():
            try:
                with ConversationWorker(
                    context.conversation_store,
                    context.conversation_discovery.client,
                    authorize=lambda job: authorize_current(context, job),
                ) as worker:
                    self.ready.set()
                    while not self.stop.is_set():
                        worker.run_once()
                        self.stop.wait(0.25)
            except Exception:
                context.conversation_store = None
                self.failed.set()
                self.ready.set()

        self.thread = threading.Thread(target=run, name="vox-conversation-worker", daemon=False)
        self.thread.start()
        self.ready.wait()
        if self.failed.is_set():
            self.thread.join()
            raise RuntimeError("CONVERSATION_WORKER_START_FAILED")

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
