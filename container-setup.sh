#!/usr/bin/env bash
# Idempotent setup for the cloud container that runs the hourly corpus sweep.
# Safe to run on every start (put it in the environment's setup script). It installs rclone if it is
# missing (pinned version, checksum verified) and checks the Drive remotes work. It never prints secrets.
# Exit 0: ready. Exit 3: not ready, with the reason on stderr (the run treats that as an error).
set -u
V="${RCLONE_VERSION:-v1.69.1}"
BIN="$HOME/.local/bin"; mkdir -p "$BIN"; export PATH="$BIN:$PATH"
fail() { echo "container-setup: $*" >&2; exit 3; }

if ! command -v rclone >/dev/null 2>&1; then
  T=$(mktemp -d); Z="rclone-$V-linux-amd64.zip"
  curl -fsS -m 120 -o "$T/$Z" "https://downloads.rclone.org/$V/$Z" || fail "rclone download failed"
  curl -fsS -m 60 -o "$T/SHA256SUMS" "https://downloads.rclone.org/$V/SHA256SUMS" || fail "rclone checksum list download failed"
  (cd "$T" && grep " $Z\$" SHA256SUMS | sha256sum -c - >/dev/null 2>&1) || fail "rclone checksum mismatch"
  python3 -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$T/$Z" "$T" || fail "rclone unzip failed"
  cp "$T/rclone-$V-linux-amd64/rclone" "$BIN/rclone" && chmod +x "$BIN/rclone" || fail "rclone install failed"
fi

# Drive remotes: use what the environment provides. Only build the config here if the key arrives as env vars.
if ! rclone listremotes 2>/dev/null | grep -q '^clients:$' || ! rclone listremotes 2>/dev/null | grep -q '^ops:$'; then
  if [ -n "${GDRIVE_SA_JSON:-}" ] && [ -n "${CLIENTS_DRIVE_ID:-}" ] && [ -n "${OPS_DRIVE_ID:-}" ]; then
    python3 "$(dirname "$0")/corpus_stage.py" setup >/dev/null || fail "corpus_stage setup failed"
  else
    fail "rclone remotes clients: and ops: are not configured, and no GDRIVE_SA_JSON/CLIENTS_DRIVE_ID/OPS_DRIVE_ID provided"
  fi
fi

# One cheap read per remote proves the credentials and the shared-drive access.
timeout 90 rclone lsf "clients:" --dirs-only --max-depth 1 >/dev/null 2>&1 || fail "cannot read the Clients shared drive"
timeout 60 rclone lsf "ops:Wisely Shared Config" --dirs-only --max-depth 1 >/dev/null 2>&1 || fail "cannot read Wisely Shared Config on the Operations drive"

# Fixes to the installed client-corpus skill that are not in the skill library yet (patches/). Applied only
# if missing, so this is safe to repeat and harmless once the library carries them.
SK=$(ls -d "$HOME"/.claude/plugins/synced/*/client-corpus*/skills/client-corpus 2>/dev/null | tail -1)
if [ -n "$SK" ]; then
  python3 "$(dirname "$0")/apply_skill_patches.py" "$SK" >/dev/null || fail "could not apply the skill patches (patches/); sync.py may be a different version"
fi
echo "container-setup: ready"
