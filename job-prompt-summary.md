Daily corpus run summary.

This is an unattended scheduled task under the Wisely service account, in a cloud container. Nobody is watching this session and nobody will answer a question while it runs. Chris Upton owns this task.

Use the client-corpus skill (it may be listed as client-corpus:client-corpus). Read its references/daily-summary.md and follow it exactly, with its scripts/unattended.py digest. Only the way the run records are read differs, as set out below.

READING THE RUN RECORDS (replaces the Google Drive for desktop mount)
There is no desktop mount here. Export PATH=$HOME/.local/bin:$PATH and STAGE_ROOT=$HOME/Library/CloudStorage/GoogleDrive-wisely-svc, then:
1. Check /work/corpus-cloud/corpus_stage.py and container-setup.sh exist, then run bash /work/corpus-cloud/container-setup.sh.
2. python3 /work/corpus-cloud/corpus_stage.py pull --fresh. It copies Wisely Shared Config (including corpus-runs) under STAGE_ROOT. Nothing is ever pushed back by this task.
3. Run unattended.py digest for the 24 hours ending now (NZ local time), with --runs-dir set to $STAGE_ROOT/Shared drives/Consulting | Operations/Wisely Shared Config/corpus-runs. Then run `python3 /work/corpus-cloud/corpus_run.py advise --hours 24` (same STAGE_ROOT): its text field says how to resolve each open problem, with the commands to run and the owner. Send one plain-text email with the digest's subject whose body is the digest's text exactly as printed, then a blank line, then the advise text. If advise fails, send the digest alone and add the line "How to resolve: see corpus-runs/tidy-queue.md; run /tidy \"Client\" then /sweep \"Client\" for each client listed."
If step 1 or 2 fails, the run records cannot be read: still send the email, with the subject "Corpus daily summary: run records not readable" and a body giving the time in NZ local time and the exact error text, because this email is the only way the maintainers hear of it. Then reply in one line saying the staging step failed.

Rules for this task:
- Connector tools may be deferred: load the Gmail send tool with ToolSearch before using it.
- Send one email, To: chris.u@wiselyglobal.tech, owen.b@wiselyglobal.tech and chris.b@wiselyglobal.tech. Never to anyone else, no cc or bcc, no attachments, no replies, no drafts left behind, and do not read any mailbox.
- Send it whatever the status, including "no runs recorded" and "run records not readable".
- Never ask a question. Write no file in Drive or in any client folder, and push nothing. Never write to monday, never touch Xero, Google Calendar, Slack or Zoom. Never delete anything.
- If the client-corpus skill is not available, send the "run records not readable" email above saying so, then reply in one line.
- Otherwise your final reply is two lines at most: the subject, then "Sent to the maintainers." or "Not sent:" with the reason.
