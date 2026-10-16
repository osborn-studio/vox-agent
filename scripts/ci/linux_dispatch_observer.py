"""Read-only polling phase of the dmserver dispatcher. No JIT/SSH or mutations.

Run with --once for a bounded snapshot. Production admission must be explicitly
approved using the exact workflow bytes in the trusted config. Even an admitted
job is only reported here; no paid fallback or runner registration is possible.
"""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from linux_dispatch_admission import admit, lease_key, policy
from linux_dispatch_admission import WORKFLOW as WORKFLOW  # Existing observer API.

ROOT = Path('/Volumes/AIMATE_WORK/CI/osvox-dmserver')
GH = '/opt/homebrew/bin/gh'


def api(path):
    result = subprocess.run([GH, 'api', '--hostname', 'github.com', path], capture_output=True, text=True, timeout=30,
                            env={'HOME':str(Path.home()), 'PATH':'/usr/bin:/bin',
                                 'GH_PROMPT_DISABLED':'1', 'GH_PAGER':'cat'})
    if result.returncode:
        raise RuntimeError('Read-only GitHub query failed')
    return json.loads(result.stdout)


def snapshot(config, *, namespace="osvox"):
    repository, _, workflow, _, _, _ = policy(namespace)
    result = {'mode':'OBSERVE_ONLY', 'production_enabled':False, 'paid_fallback_minutes':0,
              'observed_at':time.time(), 'jobs':[]}
    # Paginate in-progress/queued runs: never assume only the first page exists.
    for status in ('queued', 'in_progress'):
        for page in range(1, 11):
            runs = api(f'repos/{repository}/actions/workflows/{workflow.rsplit("/", 1)[1]}/runs?status={status}&event=push&branch=main&per_page=100&page={page}')['workflow_runs']
            for run in runs:
                if run.get('path') != workflow or run.get('event') != 'push':
                    continue
                content = api(f"repos/{repository}/contents/{workflow}?ref={run['head_sha']}")
                raw = base64.b64decode(content['content'], validate=False)
                for job_page in range(1, 11):
                    jobs = api(f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100&page={job_page}")['jobs']
                    for job in jobs:
                        accepted, reason = admit(run, job, raw, config.get('workflow_sha256'),
                                                 config.get('routing_approved', False), namespace=namespace)
                        result['jobs'].append({'key':lease_key(run,job), 'name':job['name'],
                                               'admitted':accepted,'reason':reason})
                    if len(jobs) < 100:
                        break
                else:
                    raise RuntimeError('Job pagination bound reached; fail closed')
            if len(runs) < 100:
                break
        else:
            raise RuntimeError('Run pagination bound reached; fail closed')
    result['jobs'] = list({job['key']:job for job in result['jobs']}.values())
    return result


def atomic_write(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    if path.is_symlink():
        raise RuntimeError('Refusing symlink evidence')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def trusted_config(path):
    if not path.exists():
        return {}
    if path.is_symlink() or path.resolve() != path.absolute():
        raise RuntimeError('Untrusted configuration path')
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise RuntimeError('Untrusted configuration ownership/mode')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Configuration must be an object')
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    for directory in (ROOT, ROOT/'controller', ROOT/'evidence'):
        st = directory.stat()
        if directory.resolve() != directory.absolute() or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise RuntimeError('Controller directory is not trusted')
    with (ROOT/'controller/dispatch-observer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            config_path = ROOT/'controller/dispatch-admission.json'
            try:
                config = trusted_config(config_path)
                result = snapshot(config)
            except (RuntimeError, KeyError, ValueError, TypeError, AttributeError, OSError, subprocess.TimeoutExpired) as error:
                atomic_write(ROOT/'evidence/dispatch-observer.json',
                             {'mode':'OBSERVE_ONLY','production_enabled':False,
                              'status':'ERROR_FAIL_CLOSED','error_type':type(error).__name__,
                              'observed_at':time.time()})
                if args.once:
                    raise
            else:
                atomic_write(ROOT/'evidence/dispatch-observer.json', result)
            if args.once:
                break
            time.sleep(60)


if __name__ == '__main__':
    main()
