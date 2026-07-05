# Filing methods — one-page summaries (sources named, wording our own)

The skill's Procedure names these methods; this page is the slightly longer
reference. None of this is secret sauce — the value is applying them *through
the user's own preferences file*, with a human gate before anything moves.

## PARA — sort by actionability (Tiago Forte, *Building a Second Brain*)

Four top-level buckets, ordered by how actionable the contents are:

| Bucket | What belongs | Test |
|---|---|---|
| **Projects** | Active efforts with a goal and an end date | "Will this be *done* someday?" |
| **Areas** | Ongoing responsibilities with a standard to maintain | "Am I responsible for this indefinitely?" |
| **Resources** | Topics/assets of ongoing interest | "Might I want this for reference?" |
| **Archives** | Anything inactive from the other three | "Is this dormant?" |

Key habits: move a whole project folder to Archives when it ships (cheap,
reversible); resist deep hierarchies — PARA is intentionally shallow; when a
file could go two places, prefer the more actionable bucket.

## GTD inbox-zero (David Allen, *Getting Things Done*)

Downloads and Desktop are **inboxes**: things arrive there, nothing *lives*
there. Process each item once, with a decision, not a shuffle:

1. Junk/expired installer/true duplicate → propose for deletion (human gate).
2. Reference or asset → file to its real home (PARA bucket or the user's own
   structure).
3. Belongs to an active task → into that project's folder.
4. Genuinely undecidable → an explicit "needs your call" list — small and
   temporary by design.

"Empty" is the success criterion for an inbox; "neatly sorted inbox" is a
contradiction in terms.

## Johnny.Decimal (johnnydecimal.com)

Number 10 areas × 10 categories: `11.03` = area 10-19, category 11, item 03.
Every file has exactly one home and a stable, quotable address ("it's in
12.04"). Strict and a little bureaucratic **by design** — offer it only when
the user wants that rigidity; never impose it on a working loose structure.

## Naming rules (standard research-data-management practice)

Widely taught in university RDM guides (Harvard, Stanford, MIT library
guidance among others):

- **ISO date prefix** `YYYY-MM-DD` for anything time-ordered — sorts
  chronologically as a side effect of sorting alphabetically.
- **No spaces or special characters**; use `-` or `_` (tooling- and
  script-safe, survives URLs and shells).
- **Content words, not container words**: `2026-07-04-loan-contract-draft.pdf`
  beats `document(3) copy.pdf`.
- **Version as suffix** `v01`, `v02` — never `final`, `final2`, `REAL-final`
  (the moment `final2` exists, `final` is a lie).

## Duplicate detection — size is a hint, hash is a fact

1. Group by exact byte size (cheap first pass).
2. Within a size group, compare content hashes (`sha1sum` / `md5sum` /
   `certutil -hashfile` via run_command).
3. Only matching hashes make a true duplicate. Name-pattern copies
   (`report (1).pdf`) are *candidates* until step 2 confirms.
4. Propose which copy to keep (usually: the one in a real home over the one in
   an inbox; the older path if both are filed) — the delete itself always goes
   through the human gate with a backup first.

## Duplicate decision tree — the full order, with thresholds

The one-page procedure above, expanded into the exact decision order (each
step is cheaper than the next, so never skip ahead):

1. **Size first (free).** Different byte size ⇒ *not* a duplicate, stop.
   Same size ⇒ candidate pair, go on. Never propose on size alone.
2. **Hash second (cheap).** Same content hash ⇒ **true duplicate** — safe to
   propose deleting one copy (human gate + backup as always).
   Different hash but suspicious names (`report (1).pdf`, `draft-copy.docx`,
   `photo - Copy.jpg`) ⇒ go to step 3.
3. **Content-difference check (needs judgment — this is a "needs your call",
   not a delete proposal).** Same name pattern + different hash usually means
   *versions*, not duplicates: a newer draft, a re-export, an edited photo.
   Report the pair with sizes and modified dates and let the human decide.
   The butler proposes deletions only for step-2 exact matches; near-duplicates
   are **never** auto-classified as junk.
4. **Which copy to keep** (for step-2 true duplicates): prefer the copy in a
   real filed home over the copy in an inbox (Downloads/Desktop); if both are
   filed, prefer the one whose path matches the user's structure; state the
   choice and the reason in the proposal.

Threshold notes: hashing is worth it for any same-size group; for very large
files (say over a few hundred MB) hash lazily — flag the pair first, hash only
if the human wants certainty. Zero-byte files are their own class: list them
separately (they are usually failed downloads), still behind the human gate.

## Archiving — hot / cold, by date of last use

Standard personal-archiving practice (the same actionability idea as PARA's
Archives bucket, applied by time):

- **Hot** = touched or modified recently — stays where it is. Never archive
  something the user worked on this month just because the folder looks full.
- **Cold candidate** = untouched for **~180 days** (half a year) and not on
  the user's never-touch list. Propose moving to
  `Archives/<year>/` (or the user's own archive home), grouped by year —
  `Archives/2025/…` — so restores are findable by "when did I last need it".
- The 180-day line is a **default, not a law**: tax/legal/finance documents
  stay reachable per the user's rules even when old; the preferences file
  outranks the timer.
- Archive moves are *reversible by design* (that is why they are safe to
  propose in bulk); deletions never ride along in an archive batch — separate
  section, separate confirmation, always.
- Completed projects archive as a **whole folder** (PARA habit: cheap and
  reversible), never file-by-file — a shattered project is worse than a
  dormant one.

## Where common file types usually go (defaults, user's rules outrank)

| Type | Default destination | Notes |
|---|---|---|
| Screenshots | `Archives/screenshots/<year>-<month>/` | Breed on Desktops everywhere; archive by month **after confirming** the Desktop isn't a working queue |
| Installers (`.exe`, `.dmg`, `.msi`, `.pkg`) | propose deletion ~30 days after install | Re-downloadable by definition; still behind the delete gate |
| Documents you authored | PARA bucket (Project if active, Area if ongoing) | Content words + ISO date in the name |
| Bank statements / invoices / receipts | `Documents/finance/<year>/` | Never auto-delete, whatever the age — finance is a classic never-touch |
| Photos / camera exports | user's media home, keep original filenames | Camera names (`IMG_1234`) carry order; renaming loses it |
| Compressed archives (`.zip`, `.rar`) already extracted | propose deletion, keep the extracted folder | Only when the extracted copy verifiably exists |
| Fonts, wallpapers, misc assets | `Resources/` (PARA) | Interest, not action |
| Anything you can't classify | the explicit "needs your call" list | Small and temporary by design — never a guessed destination |

## Operations that are never silent (always a human decision)

- **Any deletion** — including "obvious" junk, cache, zero-byte files,
  true duplicates. Backup lands first, every time.
- **Overwrite on move/rename** — destination already has a file with that
  name? Stop and ask; never clobber, never auto-suffix silently.
- **Batch renames** — a wrong pattern applied 200 times is 200 mistakes;
  show the full before→after list, get the nod, then execute.
- **Touching anything on the never-touch list** — the plan is wrong, not
  the list.
- **Crossing the whitelist boundary** — report, never route around.
- **Hidden files, dotfiles, system folders, application data** — out of
  scope by definition; a plan that needs them is a wrong plan.
