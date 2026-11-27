# corpus-cloud

Tools that let the Wisely `client-corpus` unattended sweeps run in a cloud container with no Google Drive for desktop mount.
Drive is staged to local disk with rclone in the same folder layout the skill expects, the skill's own scripts run against it, and only
what changed goes back.

| File | Purpose |
|---|---|
| `corpus_stage.py` | `setup`, `pull`, `status`, `push`. Pull copies the folders a run needs and snapshots them. Push sends back only changed or new files, refuses any file that changed on Drive since the pull, never deletes (the one removal is sending an already-applied pending sync's original to the Drive trash after its archive copy is verified), and reads every file back by size. |
| `corpus_run.py` | The mechanics of a run, as subcommands that print JSON: `begin [--mode changed\|nightly]`, `discover`, `stages-from-board`, `plan`, `apply-pending`, `undo`, `record-swept`, `wrap`, `finish`; nightly adds `maintenance` and `requests`. |
| `cc-env.sh` | Source it first in every shell command of a run. Finds the repo wherever it is checked out (`/work/corpus-cloud`, `/home/user/corpus-cloud`, `~/corpus-cloud`) and sets `CC`, `PATH`, `STAGE_ROOT` and `GMAIL_SWEEP`. |
| `corpus_tidy.py` | `R tidy CLIENT [--apply]`: puts a client's KB folder right wherever the evidence gives one answer. Moves only, by Drive file id (`rclone backend moveid`), never trashes. Native Google files leave the KB tree, duplicates are kept or merged when they do not conflict, dated history goes to `archive/`; a real conflict is reported with the exact question. Dry run by default. |
| `POLICY.md` | What the unattended runs decide themselves and what they hand to a person, and how a hand-off must be worded. |
| `slack_notify.py` | Posts one alert to the corpus Slack channel as the Slack app (bot), not as a person. The bot token is a Network secret on host `slack.com`; the script sends no token. `--check` runs `auth.test` and posts nothing. |
| `container-setup.sh` | Idempotent. Installs rclone (pinned, checksum-verified) if missing and proves both shared drives are readable. Put it in the environment's setup script. |
| `job-prompt.md` | The hourly scheduled-task prompt (`/sweep changed`). |
| `job-prompt-nightly.md` | The nightly scheduled-task prompt (`/sweep nightly`). |
| `job-prompt-gmail-sweep.md` | A per-person scheduled task (own account, own machine, `30 0 * * *`): reads that person's labelled Gmail and files pending syncs, which the cloud hourly run applies. This is how client email reaches the corpus. |
| `job-prompt-summary.md` | The daily summary email task (reads the run records, sends one email to the three maintainers, writes nothing). |
| `permissions.example.json` | Pre-approval list for unattended sessions (allow the run's commands and connector tools, deny anything that deletes or drafts mail). Review before use. |
| `test_corpus_run.py` | Unit tests for the helpers in `corpus_run.py`. |
| `test_corpus_tidy.py` | Unit tests for `corpus_tidy.py` (a fake Drive stands in for rclone). |
| `test_corpus_stage.sh` | Offline test (two local folders stand in for the shared drives). Run it after any change to `corpus_stage.py`. |
| `patches/` | Fixes to the installed client-corpus skill (applied by `apply_skill_patches.py`) that belong in the skill library source (`Chris Skills/Skill Sources/`): `sync-last-sweep`. Each patch is skipped once the installed skill carries the fix itself, so a newer skill release never fails container setup. The daily-digest fix (a failure or dropped flag for a client a later run swept) is upstream as of skill 13.12.0 and no longer patched. |

## Where it runs

A scheduled task (routine) in a cloud environment that has this repo as a source. The prompts find the checkout themselves, so the folder name does not matter.

The environment needs:
- **Secrets / environment variables** (set in the environment, never in the repo): `GDRIVE_SA_JSON_B64` (the Hermes service-account key JSON, base64-encoded onto one line so the variable box cannot mangle it; plain `GDRIVE_SA_JSON` also works), `CLIENTS_DRIVE_ID` (`0AGyf2DLZP-r8Uk9PVA`) and `OPS_DRIVE_ID` (`0AFBBEThGl8jnUk9PVA`). `container-setup.sh` turns them into the `rclone` config and installs `rclone` (pinned, checksum-verified) if it is missing.
- **Internet access** to `downloads.rclone.org` and `googleapis.com` (and GitHub for the checkout).
- **Network secret** for the Slack app: host `slack.com`, type Token, header `Authorization`, prefix `Bearer`, token = the app's Bot User OAuth Token (`xoxb-...`). The app needs the `chat:write` scope and must be a member of the alert channel (`/invite @appname`). The environment's network must allow `slack.com`.
- **Connectors** attached to the task: Google Drive, monday.com, Slack, and Gmail with a send tool (without a send tool the prompts post the alert to the Slack channel instead of emailing).
- A permission mode that does not prompt; see `permissions.example.json`.

Python 3 and curl are used; the client-corpus skill must be synced into the environment.

## Rules the tools keep

- Corpus files are written only through the skill's scripts, with an undo copy taken and verified first.
- Nothing is deleted. `R tidy` only moves files, into the same client folder (`Archived from KB DATE/`, `archive/`), so every action can be undone by moving the file back. Old undo folders go to the Drive trash through the connector.
- The Drive connector can change only files it created itself (Drive reports `canMoveItemWithinDrive` and `canTrash` as false on everything else, even where the account is a writer). Moves and trashes of other people's files therefore go through rclone, whose service account holds Content Manager.
- The Drive listing in `discover` is an approved replacement (owner, 24 Nov 2026) for the connector search in `unattended.md` step 2.
  The connector route stays as the fallback.
- Clients whose corpus folder has same-named files on Drive are never staged; the run records `duplicate-titles` and the nightly run (or `/tidy`) fixes them.
- An open-session marker does not hold a client back when a complete pending sync (all parts present, `closed_at` set) names that marker: the session has ended, and applying the pending sync is what closes the marker. `plan` checks this itself (`ended_session_markers`) because the skill's `open-marker` check runs before `apply-pending` and would otherwise skip the client for 24 hours. A marker no pending sync names, an in-changelog marker and one with no readable time still skip.

## Settings

`GMAIL_SWEEP` (`on`/`off`, default `off`): the service account's mailbox holds no `Clients/*` labels, so Gmail is not swept by these runs. Client email reaches the corpus through people's own `/sweep` sessions, which file pending syncs that the hourly run applies.

## Tests

```
bash test_corpus_stage.sh
python3 test_corpus_run.py
python3 test_corpus_tidy.py
python3 test_slack_notify.py
```

## Scheduled tasks (all times NZ local)

| Task | Prompt | Cron (`CRON_TZ=Pacific/Auckland`) |
|---|---|---|
| Hourly sweep | `job-prompt.md` | `7 * * * *` |
| Nightly maintenance | `job-prompt-nightly.md` | `30 1 * * *` (between the 01:07 and 02:07 hourly runs) |
| Daily summary email | `job-prompt-gmail-sweep.md` | A per-person scheduled task (own account, own machine, `30 0 * * *`): reads that person's labelled Gmail and files pending syncs, which the cloud hourly run applies. This is how client email reaches the corpus. |
| `job-prompt-summary.md` | `0 7 * * *` |

The three share one run state in `corpus-runs`: a run refuses to start while another began less than three hours ago.
