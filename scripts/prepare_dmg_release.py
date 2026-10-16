"""Resolve release inputs and preserve any existing, independently verified release."""

import json
import os
import re
import subprocess
from pathlib import Path


def prepare(env):
    tagged = env.get('GITHUB_EVENT_NAME') == 'push' and env.get('GITHUB_REF_TYPE') == 'tag'
    tag = env.get('GITHUB_REF_NAME', '') if tagged else 'v' + env.get('RELEASE_VERSION', '')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag):
        raise ValueError('Release version must be vX.Y.Z (or X.Y.Z for manual builds)')
    existing = False
    if tagged:
        result = subprocess.run(
            ['gh', 'api', '--paginate', '--slurp',
             f"repos/{env['GITHUB_REPOSITORY']}/releases?per_page=100"],
            check=True, capture_output=True, text=True,
        )
        existing = any(item['tag_name'] == tag for page in json.loads(result.stdout) for item in page)
    return {'version': tag[1:], 'skip': str(existing).lower()}


if __name__ == '__main__':
    values = prepare(os.environ)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        for key, value in values.items():
            output.write(f'{key}={value}\n')
    if values['skip'] == 'true':
        print('Existing release found; preserving its assets and publication state.')
