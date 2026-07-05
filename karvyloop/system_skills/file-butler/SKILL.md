---
name: file-butler
description: Tidy personal folders safely with a preview-first method — read-only inventory, a dry-run proposal grounded in the user's own filing preferences, explicit human confirmation before anything moves (backup before any delete), then execution with a receipt. A system template for file organization — ships with the product, never deleted by a data reset.
version: "1.0"
signature: system:file-butler
source: system
scope: user
result_reuse: dynamic
when_to_use: When the user asks to organize, tidy, archive, deduplicate, rename or restructure files and folders (Desktop / Downloads / Documents), or to design a folder structure that stays maintainable. 当需要整理文件、收拾桌面或下载文件夹、归档旧文件、清理重复文件、批量重命名、设计文件夹结构时。
tags: [文件, 整理, 整理文件, 收拾, 归档, 文件夹, 目录, 桌面, 下载文件夹, 文档目录, 重复文件, 清理文件, 重命名, 命名规范, 收纳, files, folder, folders, organize, organise, tidy, tidy up, cleanup, declutter, archive, duplicate, duplicates, rename, naming, desktop, downloads]
allowed-tools:
  - read_file
  - run_command
---

# File Butler (system template)

**Files are not data rows.** A wrong query can be re-run; a file moved to the
wrong place — or deleted — costs the human real time and sometimes real work.
So this method optimizes for *trust before tidiness*: everything is previewed,
nothing is deleted without an explicit human decision, and the plan is grounded
in the **user's own filing rules**, not a textbook ideal. This skill is a
*method*, not a canned answer.

## The accountability rule

You (atom) answer to the role; the role answers to the human. A folder that
*looks* tidy but lost or hid one file spends trust you cannot get back. When in
doubt, **propose and ask** — never act on a guess about where something belongs
or whether it is junk.

## The method library (pick per situation, name your pick in the proposal)

- **PARA** (Tiago Forte, *Building a Second Brain*): sort by *actionability*,
  not by topic — **P**rojects (active, has a goal and an end), **A**reas
  (ongoing responsibilities), **R**esources (topics of interest), **A**rchives
  (everything inactive). Default for Documents. Rule of thumb: if it has a
  deadline it's a Project; if you must maintain it forever it's an Area.
- **GTD inbox-zero** (David Allen, *Getting Things Done*): treat Downloads and
  Desktop as *inboxes* — transit stations, never storage. Each item gets one
  touch-and-decide pass: file it to its real home, archive it, or propose it
  for deletion. An inbox is "done" when it is empty, not when it is sorted.
- **Johnny.Decimal** (johnnydecimal.com): number areas and categories
  (`11.03 …`) so every file has exactly one home and a stable, quotable
  address. Offer it only when the user wants a rigid, numbered tree — it is
  strict by design.
- **Naming rules** (standard research-data-management practice, e.g.
  university RDM guides): `YYYY-MM-DD` ISO date prefix for time-ordered files;
  no spaces or special characters (`-`/`_` instead); content words over
  generic ones (`2026-07-04-loan-contract-draft` not `document(3) copy`);
  versions as `v01, v02` suffixes — never `final`, `final2`, `REAL_final`.
- **Duplicate detection**: same size is a *hint*; identical content hash
  (e.g. `sha1sum`/`md5sum` via run_command) is the *fact*. Browser-created
  `name (1).ext` copies are candidates only until hashes match. Duplicates are
  proposed for deletion (with which copy to keep and why) — never auto-deleted.

## Procedure — do not skip steps

1. **Read the user's filing preferences first — their rules outrank every
   default above.** The filled-in copy of
   `references/filing-preferences.template.md` lives in the user's own space
   and is **human-owned**. It says where their projects live, what must never
   be touched, and how they like names. Missing or ambiguous? **Ask once**,
   then remember the answer as a preference candidate — do not invent a rule.

2. **Inventory, read-only.** Scan only the whitelisted folders (list files
   with type, size, modified date; fingerprint likely duplicates by size, then
   hash). Touch nothing. Skip hidden files, system folders and application
   data entirely — they are out of scope by definition.

3. **Propose a dry-run plan.** For every item: `source → destination — reason`,
   with the method named (PARA / inbox-zero / naming rule / duplicate). Unsure
   items go to an explicit **"needs your call"** section instead of a guessed
   destination. Deletions (true duplicates, obvious cache junk) are a separate,
   clearly-marked section — they need the human's explicit confirmation and a
   backup path stated up front.

4. **Wait for confirmation — the human's edit is signal.** Execute only what
   was confirmed. If the human redirects a destination or vetoes a class of
   moves, record that as a preference candidate for their preferences file:
   that correction is the most valuable thing this job produces.

5. **Execute exactly the confirmed plan, then verify and hand a receipt.**
   Backups land before any delete. After execution, verify **nothing was
   lost**: every inventoried file exists in a planned destination (or backup).
   The receipt lists: moved / renamed / skipped (and why) / new preference
   candidates.

## When you cannot proceed

No preferences file and the human is unavailable to answer? Deliver the
inventory and the dry-run proposal with your assumptions clearly marked — and
stop there. An unexecuted good plan is a success; an executed guess is not.
A path outside the granted whitelist is a boundary, not a challenge: report it,
never route around it.

## What crystallizes

The reusable asset is this **method** (inventory → grounded proposal → human
gate → verified execution) plus, over repeated use, the user's growing
**filing-preferences file** — their structure, their never-touch list, their
naming taste. That file is human-owned and machine-read: it is why the tenth
tidy-up proposal looks like the user wrote it themselves.
