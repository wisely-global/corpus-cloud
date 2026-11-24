#!/usr/bin/env bash
# Source this at the start of every shell command in a corpus run:  . <repo>/cc-env.sh
# It works wherever the repo is checked out (/work/corpus-cloud, /home/user/corpus-cloud, ~/corpus-cloud):
# it sets CC (the repo folder), puts ~/.local/bin on PATH (rclone lives there) and sets the staging defaults.
CC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"; export CC
export PATH="$HOME/.local/bin:$PATH"
export STAGE_ROOT="${STAGE_ROOT:-$HOME/Library/CloudStorage/GoogleDrive-wisely-svc}"
export GMAIL_SWEEP="${GMAIL_SWEEP:-off}"
