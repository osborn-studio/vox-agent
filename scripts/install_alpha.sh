#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER_DIR="$ROOT_DIR/dist/voxlog2-alpha-installer-$(uname -m | sed 's/x86_64/intel/;s/arm64/arm64/')"

"$ROOT_DIR/scripts/build_alpha.sh"
VOXLOG_AUTO_OPEN_APP="${VOXLOG_AUTO_OPEN_APP:-1}" bash "$INSTALLER_DIR/Install VoxLog2.command"
