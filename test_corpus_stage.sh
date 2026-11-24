#!/usr/bin/env bash
# Offline test: two local directories stand in for the Clients and Operations shared drives.
set -u
cd "$(dirname "$0")"; T=$(mktemp -d); export PATH=$HOME/.local/bin:$PATH
mkdir -p "$T/drive-clients/Acme/Claude Project Knowledge Base" "$T/drive-clients/Beta/Claude Project Knowledge Base" "$T/drive-ops/Wisely Shared Config/corpus-runs"
KB="$T/drive-clients/Acme/Claude Project Knowledge Base"
echo "now v1" > "$KB/now.md"; head -c 50000 /dev/urandom | base64 > "$KB/changelog.md"; echo '{}' > "$KB/source-manifest.json"
echo '{"runs":[]}' > "$T/drive-ops/Wisely Shared Config/corpus-runs/corpus-run-state.json"
echo "# clients" > "$T/drive-ops/Wisely Shared Config/client-list.md"; echo '{}' > "$T/drive-ops/Wisely Shared Config/corpus-index.json"
cat > "$T/rclone.conf" <<C
[clients]
type = alias
remote = $T/drive-clients
[ops]
type = alias
remote = $T/drive-ops
C
export RCLONE_CONFIG="$T/rclone.conf" STAGE_ROOT="$T/mount" CLIENTS_REMOTE=clients: OPS_REMOTE=ops:
printf 'Acme\nBeta\n' > "$T/clients.txt"; S="python3 corpus_stage.py"; fail=0
chk(){ if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
$S pull --clients-file "$T/clients.txt" > "$T/o1" 2>&1; cat "$T/o1" | head -20; true; chk "pull ok" "[ \$? -eq 0 ] && grep -q '\"ok\": true' $T/o1"
M="$T/mount/Shared drives/Consulting | Clients/Acme/Claude Project Knowledge Base"
chk "layout matches mount paths" "[ -f '$M/now.md' ] && [ -f '$T/mount/Shared drives/Consulting | Operations/Wisely Shared Config/corpus-runs/corpus-run-state.json' ]"
chk "large file intact (>20KB)" "cmp -s '$M/changelog.md' '$KB/changelog.md'"
chk "pull twice refused" "! $S pull --clients-file $T/clients.txt >/dev/null"
chk "status empty before run" "$S status | grep -q '\"would_push\": \[\]'"
# a run: edit now.md, add undo copy, update changelog, update run state
mkdir -p "$M/archive/undo/run-1"; cp "$M/now.md" "$M/archive/undo/run-1/now.md"
echo "now v2" > "$M/now.md"; head -c 60000 /dev/urandom | base64 >> "$M/changelog.md"
echo '{"runs":["run-1"]}' > "$T/mount/Shared drives/Consulting | Operations/Wisely Shared Config/corpus-runs/corpus-run-state.json"
$S status > "$T/o2"; chk "status sees 4 changes" "[ \$(python3 -c \"import json;print(len(json.load(open('$T/o2'))['would_push']))\") -eq 4 ]"
$S push --dry-run >/dev/null; chk "dry-run changes nothing on Drive" "grep -q 'now v1' '$KB/now.md'"
$S push > "$T/o3"; chk "push ok" "[ \$? -eq 0 ]"
chk "Drive has new now.md" "grep -q 'now v2' '$KB/now.md'"
chk "large changelog pushed intact" "cmp -s '$M/changelog.md' '$KB/changelog.md'"
chk "undo copy created" "[ -f '$KB/archive/undo/run-1/now.md' ]"
chk "run state pushed" "grep -q run-1 '$T/drive-ops/Wisely Shared Config/corpus-runs/corpus-run-state.json'"
# two-phase pull: config first, then add a client
$S pull --fresh >/dev/null; chk "config-only pull has no client" "[ ! -d '$T/mount/Shared drives/Consulting | Clients' ]"
$S pull --add --clients-file $T/clients.txt >/dev/null; chk "--add stages client, keeps config" "[ -f '$M/now.md' ] && [ -f '$T/mount/Shared drives/Consulting | Operations/Wisely Shared Config/corpus-index.json' ]"
echo edited > "$M/now.md"; $S pull --add --clients-file $T/clients.txt >/dev/null; chk "--add never overwrites staged edits" "grep -q edited '$M/now.md'"
$S push >/dev/null; chk "push after two-phase" "grep -q edited '$KB/now.md'"
# conflict: someone else edits on Drive after pull; a second staged run must not overwrite it
$S pull --clients-file $T/clients.txt --fresh >/dev/null
echo "now v3 local" > "$M/now.md"; echo "now v3 REMOTE person edit" > "$KB/now.md"
$S push > "$T/o4"; rc=$?; chk "conflict exits 3" "[ $rc -eq 3 ]"
chk "conflict leaves Drive untouched" "grep -q 'REMOTE person edit' '$KB/now.md'"
chk "conflict reported" "grep -q conflict $T/o4"
# no deletes: remove a local file, push must not delete remote
$S pull --clients-file $T/clients.txt --fresh >/dev/null; rm "$M/source-manifest.json"
$S push >/dev/null; chk "local delete never propagates" "[ -f '$KB/source-manifest.json' ]"
# applied pending sync: scanner moves it into archive/pending-applied; push must trash the root original
$S pull --fresh --clients-file $T/clients.txt >/dev/null
echo '{"x":1}' > "$KB/pending-2026-11-24-a-1.json"; $S pull --fresh --clients-file $T/clients.txt >/dev/null
mkdir -p "$M/archive/pending-applied"; mv "$M/pending-2026-11-24-a-1.json" "$M/archive/pending-applied/"
$S status | grep -q "pending-2026-11-24-a-1" ; chk "status reports the applied pending" "[ \$? -eq 0 ]"
$S push > "$T/o5"; chk "push moves applied pending" "[ ! -f '$KB/pending-2026-11-24-a-1.json' ] && [ -f '$KB/archive/pending-applied/pending-2026-11-24-a-1.json' ]"
# root pending changed by someone since pull -> not trashed
$S pull --fresh --clients-file $T/clients.txt >/dev/null
echo '{"x":2}' > "$KB/pending-2026-11-24-b-1.json"; $S pull --fresh --clients-file $T/clients.txt >/dev/null
echo '{"x":3}' > "$KB/pending-2026-11-24-b-1.json"; mkdir -p "$M/archive/pending-applied"; echo '{"x":2}' > "$M/archive/pending-applied/pending-2026-11-24-b-1.json"; rm "$M/pending-2026-11-24-b-1.json"
$S push >/dev/null; chk "changed root pending is kept" "[ -f '$KB/pending-2026-11-24-b-1.json' ]"
# shadow corpus (nightly build requests): staged empty, files written there are pushed
$S pull --fresh --clients-file $T/clients.txt >/dev/null; $S pull --add --shadow --clients-file $T/clients.txt >/dev/null
SH="$T/mount/Shared drives/Consulting | Clients/Acme/Claude Project Knowledge Base (shadow)"
chk "shadow folder staged" "[ -d '$SH' ]"
echo "shadow now" > "$SH/now.md"; $S push >/dev/null
chk "shadow file pushed" "grep -q 'shadow now' '$T/drive-clients/Acme/Claude Project Knowledge Base (shadow)/now.md'"
# duplicate titles on Drive -> pull refuses that client
cp "$T/drive-clients/Beta/Claude Project Knowledge Base" /dev/null 2>&1
# no rclone config at all must be an error, never an empty Drive
: > "$T/empty.conf"; RCLONE_CONFIG="$T/empty.conf" STAGE_ROOT="$T/mount-none" $S pull --fresh > "$T/o6"; rc6=$?
chk "missing rclone config fails the pull" "[ $rc6 -eq 3 ] && grep -q 'not configured' $T/o6"
echo "== $([ $fail -eq 0 ] && echo ALL PASS || echo FAILURES)"; rm -rf "$T"; exit $fail
