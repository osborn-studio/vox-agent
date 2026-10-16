"""Environment for CLI agents launched by a macOS GUI process."""

import os
from pathlib import Path
import sys


def cli_environment() -> dict[str, str]:
    """Preserve explicit PATH precedence; add common macOS install locations.

    Do not source shell profiles: they may be interactive or execute arbitrary
    startup commands. The child also needs PATH for scripts using env node.
    """
    environment = os.environ.copy()
    if sys.platform == "darwin":
        paths = environment.get("PATH", os.defpath).split(os.pathsep)
        for directory in (str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"):
            if directory not in paths:
                paths.append(directory)
        environment["PATH"] = os.pathsep.join(paths)
    return environment
