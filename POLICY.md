# What the unattended runs decide, and what they hand to a person

**The rule.** An unattended run does everything that does not need a person to mediate between two facts.
It hands something over only when the evidence it can see contains no single answer: two sources contradict each
other on something that matters, or the fact that is needed is not in any source. Any other hand-off is a defect in
the system, not a feature, and is counted as one (see "Measuring it").

This is policy for the runs in this repo (`job-prompt.md`, `job-prompt-nightly.md`) and the input to the next
client-corpus skill build.

## The three tiers

| Tier | When | The run does | Examples |
|---|---|---|---|
| **1. A rule decides** | One answer follows from the evidence | Acts, verifies, logs | Byte-identical duplicate; native Google file inside the KB tree; dated history beside a live canonical file; a queue row for a problem that no longer exists; a failure list with no pending file beside it; a duplicate whose twin is a strict superset |
| **2. Precedence decides** | The evidence differs but the sources have a written order | Acts on the ordering, keeps the loser, says so in the changelog | Two copies that merge without any colliding key (union, newer timestamp wins); the Accounts board decides a client's stage and which Drive folder is the client's; the higher `Version:` wins a current-state fork |
| **3. A person decides** | No single answer in the evidence | Does not choose. Hands over the exact question | Two copies give different values for the same key; a stage no source states; a registry row that does not exist |

Consensus first. Where two copies of something can be merged without any key holding two different values, the run
merges them. It flags only the keys that collide, and names them.

## Safeguards (these are mechanisms, not hand-offs)

Acting alone is safe because of how it acts, not because someone checks first:

1. **Move, never delete.** Files go to a sibling folder or `archive/` in the same client folder, so any action is
   undone by moving the file back. A move is a Drive parent change through the API (`drive_api.py`), which keeps the
   file's id, content and history. It is not `rclone backend moveid`: for a native Google Doc that falls back to
   copy then delete, and on 28 Nov 2026 it trashed three Flo & Frankie files that never reached their destination
   (all three restored; nothing lost). "Never delete" is only true if it is checked, so safeguard 4 checks it.
2. **Identify files by Drive id**, not by path. Drive allows two files with one name.
3. **Re-check before acting.** A file that changed since it was planned is skipped, not overwritten.
4. **Read back after acting.** The file must be in its destination, in no other folder, and **not trashed**. A file
   found trashed is restored at once. One failed read-back stops the whole run; three failed operations in a row stop it too.
5. **Evidence for every action**: why (byte-identical, superset, dated history), which files, and where they went, in the run record.
6. **Core corpus files are never touched** by the cleanup (`current-state.md`, `now.md`, `changelog.md`, `source-manifest.json`, the registers).
7. **A cap per client per night** (40 operations). The rest waits.
8. **Dry run first.** `R tidy CLIENT` prints the plan; `--apply` carries it out.

## Who may do what

| Actor | Can | Cannot |
|---|---|---|
| Drive connector (`ai@wiselyglobal.tech`) | Read everything; change only files it created itself | Move or trash files made by anyone or anything else, even where the account is a writer |
| Service account (Content Manager on the Clients drive), used by rclone for listing and reading and by `drive_api.py` for moves | Move any file; create folders; restore a file from the trash | Permanently delete (and the tools never ask it to) |

So moves and cleanups of existing files go through `R tidy` and the service account, never the connector.

## Reading a tidy plan

- A destination such as `Archived from KB 2026-11-28/archive/1Zg3OTLc` is a folder: the file keeps its name inside a
  folder named for the first eight characters of its Drive id, so two files with one name never collide.
- The date in `Archived from KB DATE` is NZ local.
- Native Google files show in rclone listings as `.docx` with size -1; the plan finds them by MIME type.
- `mode` says dry-run or apply. After an apply, `applied` is true only when every operation succeeded; read `summary`
  and `problems`. After three failed operations in a row the rest are `not-attempted`.
- Needs the service-account key in the environment (`GDRIVE_SA_JSON_B64`) and `cryptography` or `openssl` to sign the token request.

## Corpus builds: staging, not approval

A build for a client that has no corpus is Tier 1: nothing exists to overwrite, so there is nothing for a person to
protect, and the facts a reviewer could add (is this the right client, the right folder, a client at all) are already
decided by the Accounts board (Client status, Client ID, the folder link). The build is created in the shadow folder
only because that is **staging**: a half-built corpus never appears as the live one. It goes live with `R promote`, a
Drive folder rename (metadata only, atomic, every file id kept), when all of these hold:

- the skill's promote checks pass: the four core files exist, the manifest parses, `current-state.md` has a Version line,
  both hot files are within budget, and no live corpus exists to overwrite;
- the Accounts board says the client is a **Client** and the item has a Client ID.

If a check fails the shadow stays and the run retries next night; the failing check is named. People review the facts
in the corpus when they use it (corrections go in `corrections-log.md`), not the build process.

## Conflict cards

A hand-off is a **card**, derived fresh from the live board and Drive on every run (`R stages-from-board` writes
`corpus-runs/conflicts.json`, `R advise` prints it). A card disappears the moment the conflict is fixed, so there is
nothing to close and no stale row. Every card carries:

1. **What conflicts**, in one sentence.
2. **The facts side by side**, each with its link: both Accounts items (status, Client ID, short name, created date)
   and the Drive folder each points at (file count, corpus present or not, newest change, empty template or not), or
   both folders and which one the board links.
3. **What it blocks**, and what it does not (nothing is lost; which client is not swept).
4. **The exact question**, answerable in a word.
5. **What to do for each answer**, as steps.
6. **What happens next**, and when the card goes away.

Built: duplicate Accounts items for one client (`Serko` and `Serko Ltd.`), two top-level Drive folders with one name,
and an Account Status label the runs do not know. The last asks what the label means; a person answers by adding it to
`status-map.json` in Wisely Shared Config (`{"Customer": "client"}`), and the next run reads it.

## How a hand-off must be worded

"Needs a person" on its own is a defect. A hand-off states:

- **The question**: which two facts conflict, or which fact is missing, with the file and the key.
- **The evidence the run saw**, in a line (names and keys only, no client content).
- **The command that resolves it** and what happens next ("the next hourly run picks it up").

Examples of the form:

> Mossop: `source-manifest-v5.json` exists twice and the copies disagree: `$.sources[id=src-3].status` is "open" in one and "closed" in the other. Which is right?

> OneNZ: not swept, no stage. The Accounts board has no item whose Client Folder G Drive link is this Drive folder. In monday (wiselyglobal, Accounts) find or create the item, set Client Folder G Drive to the folder that contains Claude Project Knowledge Base, set Account Status to Client, Past Client or Buying process, then run `/sweep "OneNZ"`.

## Where the board decides

The Accounts board is the authority for **stage** and for **which Drive folder is a client's**. Where the registry
(`client-list.md`) disagrees with the board, the board wins. Where the board has no answer (no item, no link, no
status), that is a missing fact and goes to a person, with the steps above.

## Built, and not yet built

| Built | Not yet built |
|---|---|
| `R tidy`: native Google files, same-path duplicates (identical, superset, clean JSON merge), dated history | Choosing between two top-level client folders of one name by the board's folder link (needs staging by folder id instead of by name); today it is a conflict card |
| Conflict cards: duplicate Accounts items, duplicate top-level folders, unknown Account Status | Merging the contents of two Accounts items or two folders (a person does it, from the card's steps) |
| `R promote`: shadow corpus goes live by atomic rename after the checks and the board identity check | Verified end to end on a real build (the first one is OneNZ) |
| Live check in `R maintenance`: a queue row whose problem is gone is reported as already resolved | Resolving queue rows by registry alias, and clearing rows when a tidy finishes |
| Stale queue rows for a failure list with nothing pending; re-runnable `finish`; retried read-back on push | Pointer repair when a file a manifest row names has moved |
| | Collapsing duplicate **core** files, and current-state forks (flagged today) |
| | Text (non-JSON) merges beyond "one contains the other" |
| | Expiring old undo folders: the nightly trashes them through the connector, but rclone creates them, so the connector probably cannot (not yet seen to fail; the first expiry will show it). They should be moved by `R tidy`-style moves instead. |

## Measuring it

Each hand-off raised by a run is a count. The aim is that every one is a Tier 3 case with a stated question.
A hand-off that fails that test (no question, a problem the live folder does not show, a rule that could have
decided) is a bug to fix in the system. The owner's sampling review (run 50) should include a sample of the
actions Tier 1 and 2 took, not only the hand-offs.
