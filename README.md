# corpus-cloud

Tools that let the Wisely `client-corpus` unattended sweeps run in a cloud container with no Google Drive for desktop mount.
Drive is staged to local disk with rclone in the same folder layout the skill expects, the skill's own scripts run against it, and only
what changed goes back.

| File | Purpose |
|---|---|
| `corpus_stage.py` | `setup`, `pull`, `status`, `push`. Pull copies the folders a run needs and snapshots them. Push sends back only changed or new files, refuses any file that changed on Drive since the pull, never deletes (the one removal is sending an already-applied pending sync's original to the Drive trash after its archive copy is verified), and reads every file back by size. |
| `corpus_run.py` | The mechanics of a run, as subcommands that print JSON: `begin [--mode changed\|nightly]`, `discover`, `stages-from-board`, `plan`, `apply-pending`, `undo`, `record-swept`, `wrap`, `finish`; nightly adds `maintenance` and `requests`. |
| `container-setup.sh` | Idempotent. Installs rclone (pinned, checksum-verified) if missing and proves both shared drives are readable. Put it in the environment's setup script. |
| `job-prompt.md` | The hourly scheduled-task prompt (`/sweep changed`). |
| `job-prompt-nightly.md` | The nightly scheduled-task prompt (`/sweep nightly`). |
| `job-prompt-summary.md` | The daily summary email task (reads the run records, sends one email to the three maintainers, writes nothing). |
| `permissions.example.json` | Pre-approval list for unattended sessions (allow the run's commands and connector tools, deny anything that deletes or drafts mail). Review before use. |
| `test_corpus_run.py` | Unit tests for the helpers in `corpus_run.py`. |
| `test_corpus_stage.sh` | Offline test (two local folders stand in for the shared drives). Run it after any change to `corpus_stage.py`. |
| `patches/` | Fixes to the installed client-corpus skill that belong in the skill library source (`Chris Skills/Skill Sources/`). |

## Where it runs

Check this repo out at `/work/corpus-cloud` in the cloud environment (the prompts assume that path). The run checks the three tool files exist and
reports an error if they do not.

Needs: Python 3, curl, the `clients:` and `ops:` rclone remotes (Hermes service account, read/write on `Consulting | Clients` and
`Consulting | Operations`), and the client-corpus skill installed.

## Rules the tools keep

- Corpus files are written only through the skill's scripts, with an undo copy taken and verified first.
- Nothing is deleted. Old undo folders go to the Drive trash through the connector.
- The Drive listing in `discover` is an approved replacement (owner, 24 Nov 2026) for the connector search in `unattended.md` step 2.
  The connector route stays as the fallback.
- Clients whose corpus folder has same-named files on Drive are never staged; the run records `duplicate-titles` and the nightly run (or `/tidy`) fixes them.

## Settings

`GMAIL_SWEEP` (`on`/`off`, default `off`): the service account's mailbox holds no `Clients/*` labels, so Gmail is not swept by these runs. Client email reaches the corpus through people's own `/sweep` sessions, which file pending syncs that the hourly run applies.

## Tests

```
bash test_corpus_stage.sh
python3 test_corpus_run.py
```

## Scheduled tasks (all times NZ local)

| Task | Prompt | Cron (`CRON_TZ=Pacific/Auckland`) |
|---|---|---|
| Hourly sweep | `job-prompt.md` | `7 * * * *` |
| Nightly maintenance | `job-prompt-nightly.md` | `30 1 * * *` (between the 01:07 and 02:07 hourly runs) |
| Daily summary email | `job-prompt-summary.md` | `0 7 * * *` |

The three share one run state in `corpus-runs`: a run refuses to start while another began less than three hours ago.
