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
