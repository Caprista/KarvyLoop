---
name: meeting-notes
description: Turn a meeting transcript into minutes people can act on — decisions with who-decided-and-why, action items as who/what/by-when, open questions kept separate, and unknown terms checked against the team's human-owned glossary instead of guessed. Input is text (a transcript or notes you already have); this skill does not transcribe audio. A system template — ships with the product, never deleted by a data reset.
version: "1.0"
signature: system:meeting-notes
source: system
scope: user
result_reuse: dynamic
when_to_use: When the user pastes or points to a meeting transcript / rough notes and wants structured minutes, action items, or decision records. 当需要把会议文字稿/转写稿/粗记录整理成会议纪要、提取行动项和决策、生成会议总结时(输入是文字,不做录音转写)。
tags: [会议, 纪要, 会议纪要, 会议记录, 行动项, 待办, 决策记录, 转写稿, 文字稿, 例会, 周会, 复盘, 会议总结, meeting, meetings, minutes, meeting notes, action items, transcript, notes, summary, decisions, follow-up, standup, retro]
allowed-tools:
  - read_file
---

# Meeting Notes (system template)

**Minutes are written for the people who weren't there** — and for everyone,
two weeks later, arguing about what was decided. So the unit of value is not a
summary of what was *said*; it is an auditable record of what was **decided**,
what will be **done**, and what is still **open**. This skill is a *method*,
not a canned answer.

**Honest input contract:** this skill consumes **text** — a transcript exported
from your meeting tool (Tencent Meeting, Feishu Minutes, Otter, Teams…) or
whatever notes you pasted. It does **not** transcribe audio; there is no ASR in
this system today, and pretending otherwise would produce fabricated minutes.

## The accountability rule

You (atom) answer to the role; the role answers to the human. A minute that
states a decision nobody made — or silently expands a term you didn't actually
understand — is worse than a gap marked "unconfirmed". When in doubt, **record
the uncertainty**, never a confident invention.

## The three-bucket rule (keep them separate, always)

Standard team-practice pattern (see e.g. Atlassian Team Playbook's meeting
notes guidance):

1. **Decisions** — what was settled, *who* settled it, and the stated basis.
   No owner-of-the-decision identifiable? It goes to bucket 3, not here.
2. **Action items** — three required elements: **who / what / by-when**.
   Any element missing → the item is recorded but flagged **"needs
   confirmation"** (an action item without an owner is a wish, not a task).
3. **Open questions / needs confirmation** — everything raised but not
   settled, plus every flag from the rules above. This bucket being visible is
   a feature: it is the next meeting's agenda.

## Procedure — do not skip steps

1. **Read the transcript once for structure.** Split by topic (agenda item),
   not by timestamp. Note who is speaking where, if the transcript says.

2. **Extract per topic, into the three buckets.** For each decision: who
   decided + the reason given in the room (quote or close paraphrase, with the
   speaker). For each action item: who / what / by-when — flag any missing
   element. Also collect explicitly raised risks.

3. **Check unknown terms against the glossary — never expand by guessing.**
   The team's term sheet is the filled-in copy of
   `references/glossary.template.md` (**human-owned**, lives in the user's own
   space — it is the meeting domain's semantic layer). A term not in it gets
   recorded as-is and flagged "term: needs confirmation". Auto-inventing an
   expansion is how fabricated minutes are born.

4. **Output markdown in the user's preferred shape.** Default order: header
   (meeting, date, attendees if known) → decisions → action items (a table:
   who | what | by-when | status) → open questions → appendix of flagged
   terms. If the user has a fixed template, theirs wins.

5. **After the meeting: propose sediment, don't silently write.** New terms
   (once the human confirms an expansion) are candidates for the glossary; new
   standing decisions are candidates for the knowledge base. Propose them in
   the receipt — the human keeps their glossary, the pipeline handles the
   rest. Never write canned content into anyone's knowledge store.

## When you cannot proceed

Transcript too garbled to attribute speakers? Say so and produce what is
extractable, with attribution marked "unclear" — do not assign quotes to
guessed speakers. Asked to work from an audio file? Decline honestly: point
the user to their meeting tool's export, then continue from that text.

## What crystallizes

The reusable asset is this **method** (three buckets, who/what/when, glossary
gate) plus, over repeated use, the team's growing **glossary** — from 0 to
dozens of confirmed terms, a file you can watch get longer. That growth is
real and visible; no percentage needs to be invented for it.
