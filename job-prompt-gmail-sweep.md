Gmail sweep for my clients.

This is an unattended scheduled task under MY OWN Claude account, on my own machine. Nobody is watching it. It is not the cloud sweep: the cloud hourly and nightly runs cannot read Gmail, because only a person's own mailbox holds the Clients/<name> labels. This task is how client email reaches the corpus. It reads my labelled mail and files a pending sync for each client; the cloud hourly run applies it (the next run at seven minutes past the hour).

Use the client-corpus skill (it may be listed as client-corpus:client-corpus). Read its references/gmail-ingest.md and references/sync-card.md and follow them. Write nothing to any corpus file directly: the only thing this task creates is a pending sync file per client, by the skill's own scripts/sync.py pending.

Steps
1. Find my address: the signed-in Gmail account. Call it ME. List my Gmail labels (the Gmail list-labels tool, loaded with ToolSearch first) and take those named exactly Clients/<name>.
2. For each such label, resolve the client the skill's way: `python3 scripts/sync.py resolve --index "<Wisely Shared Config>/corpus-index.json" --name "<name>"` (the index is in the Wisely Shared Config folder of the Consulting | Operations shared drive; on the desktop mount it is under ~/Library/CloudStorage/GoogleDrive-*/Shared drives/; otherwise read corpus-index.json through the Drive connector). A label with no single exact client, or a client with no corpus, is skipped and named in the final reply: never guess. Skip prospects.
3. Work at most 8 clients a run, the ones whose labelled mail is newest first. For each: read the client's source-manifest.json (mount or Drive connector read), then search by LABEL DISPLAY NAME: label:"Clients/<name>" after:<this mailbox's coverage or last_sweep date for ME>. Dedupe with `scripts/email_dedupe.py diff --manifest <manifest.json> --messages <messages.json> --mailbox ME`. If a search returns more than about 30 messages, or this is my first sweep of the client, use the large-ingest procedure in gmail-ingest.md (scripts/email_triage.py plan and merge, Haiku readers for the chunks).
4. Fold what is new into a delta exactly as sync-card.md describes: kind sweep, member ME, a summary of at most 800 bytes that names the sources not covered (Drive, monday and Zoom are not swept by this task), manifest_rows and gmail_coverage from the merge (every Gmail row carries sender, date and subject; depth snippet when you did not read the body), and now_md patches only when the picture moved. Nothing new from Gmail means no delta for that client.
5. Check and file it: `python3 scripts/sync.py pending --delta delta.json --out OUT --hot-dir HOT --marker MARKER` (HOT holds the now.md and current-state.md you read; on the mount it is the client's Claude Project Knowledge Base folder), then create each file it prints in that KB folder (mount copy, or the Drive connector's create_file; they are new files, no overwrite). A delta that the dry run rejects is not filed: name the client and the reason in the final reply.

Rules for this task
- Connector tools may be deferred: load each one with ToolSearch before its first use.
- Gmail is read only. Never send, draft, label, archive or delete mail. Never write to monday, Xero, Google Calendar, Zoom or Slack. Never send email.
- Never ask a question. Anything that would need one is skipped and named in the final reply.
- Never delete anything. Never edit a corpus file, a manifest or a changelog directly.
- Quote no email content in the final reply: client names and counts only.
- If the client-corpus skill or the Gmail connector is not available, stop and reply in one line saying which.

Final reply, three lines at most: how many clients had new mail and how many pending syncs were filed; the clients skipped and why (names only); "Applies within the hour." when anything was filed.

Schedule (my own scheduled task, NZ local): `30 0 * * *`, an hour before the cloud nightly (01:30), so the 01:07 hourly run applies the syncs and the nightly sees fresh state. Each maintainer sets up one under their own account; the corpus de-duplicates mail that two people both hold.
