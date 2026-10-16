"""Bounded GitHub transport for the Mac controller, with no workflow cancel API.

Only an observed HTTP 404 on an exact runner lookup is absence. Never expose
response/error bodies: registration responses may contain JIT credentials.
"""

import json
from pathlib import Path
import re
import subprocess


ORG = "orgs/osborn-studio/actions"
REPO = "repos/osborn-studio/osvox/actions"
RUNNER = re.compile(ORG + r"/runners/[1-9][0-9]{0,19}")
READ = re.compile(
    r"(?:" + ORG + r"/runner-groups/3(?:/runners\?per_page=100&page=[1-9][0-9]?)?"
    r"|" + ORG + r"/runner-groups/3/repositories\?per_page=100"
    r"|" + REPO + r"/(?:jobs|runs)/[1-9][0-9]{0,19}"
    r"|" + REPO + r"/variables/OSVOX_MACOS_ISOLATED_RUNNERS"
    r"|" + REPO + r"/workflows/test-engine\.yml/runs\?status=(?:queued|in_progress)&event=push&branch=main&per_page=100&page=[1-9][0-9]?"
    r"|" + REPO + r"/workflows/macos-fleet-qualification\.yml/runs\?status=(?:queued|in_progress)&event=push&branch=codex/macos-fleet-qualification&per_page=100&page=[1-9][0-9]?"
    r"|" + REPO + r"/runs/[1-9][0-9]{0,19}/attempts/[1-9][0-9]{0,9}/jobs\?per_page=100&page=[1-9][0-9]?"
    r"|repos/osborn-studio/osvox/contents/\.github/workflows/(?:test-engine|macos-fleet-qualification)\.yml\?ref=[0-9a-f]{40})"
)


class GitHubAPI:
    def __init__(self, *, run=subprocess.run):
        self.run = run

    def __call__(self, endpoint, method="GET", body=None):
        runner = isinstance(endpoint, str) and RUNNER.fullmatch(endpoint)
        allowed = (
            method == "GET" and (runner or isinstance(endpoint, str) and READ.fullmatch(endpoint))
            or method == "DELETE" and runner
            or method == "POST" and endpoint == ORG + "/runners/generate-jitconfig"
        )
        if not allowed or (body is not None and method != "POST"):
            raise ValueError("Unapproved Mac controller request")
        command = ["/opt/homebrew/bin/gh", "api", "--hostname", "github.com",
                   endpoint, "--method", method, "--include"]
        if body is not None:
            command += ["--input", "-"]
        try:
            response = self.run(
                command, input=json.dumps(body) if body is not None else None,
                stdin=None if body is not None else subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
                env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin",
                     "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("GitHub transport unavailable") from None
        raw = response.stdout.replace("\r\n", "\n")
        header, separator, payload = raw.partition("\n\n")
        status = re.match(r"HTTP/\S+ ([0-9]{3})(?: |\n|$)", header)
        if not separator or not status:
            raise RuntimeError("GitHub HTTP status unconfirmed")
        code = int(status[1])
        if code == 404 and method == "GET" and runner:
            return None
        if response.returncode or code not in (200, 201, 204):
            raise RuntimeError(f"GitHub HTTP request failed ({code})")
        if code == 204 and method == "DELETE" and not payload.strip():
            return {}
        try:
            value = json.loads(payload)
        except ValueError:
            raise ValueError("Malformed GitHub response") from None
        if not isinstance(value, dict):
            raise ValueError("Invalid GitHub response shape")
        return value
