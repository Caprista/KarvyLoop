# Minutes templates by meeting type — reference (wording our own, sources named)

The skill's three buckets (decisions / action items / open questions) are
invariant; what changes per meeting type is **which sections surround them and
what gets emphasized**. These shapes follow widely-taught team practice (see
e.g. Atlassian Team Playbook's meeting-notes and DACI guidance; SMART criteria
after Doran, 1981, *Management Review*). The user's own template, once they
have one, outranks all of these.

## Weekly sync / 周会

Focus: continuity — last week's action items come **first**, not last.

```markdown
# <team> weekly sync — YYYY-MM-DD
Attendees: … (absent: …)

## Review of last week's action items
| who | what | was due | status (done / carried / dropped-why) |

## Decisions
(who decided + stated basis, per the skill's rules)

## New action items
| who | what | by-when | status |

## Open questions / needs confirmation
## Parking lot (raised, out of scope this week)
```

Carried-over items keep their original due date visibly ("was due 07-01,
carried to 07-08") — silently refreshing a deadline hides slippage.

## Review meeting (design / plan / doc review) / 评审会

Focus: the verdict on the thing under review, and what must change.

```markdown
# Review: <artifact name + version> — YYYY-MM-DD
Reviewers: …   Author(s): …

## Verdict
Approved / approved-with-changes / rejected — decided by <who>, basis: <…>

## Required changes (blocking)
| # | change requested | raised by | owner | by-when |

## Suggestions (non-blocking)
## Open questions / needs confirmation
```

The verdict is a decision like any other: no identifiable decider ⇒ it goes
to open questions, and the minutes say the review is **not concluded**.

## 1-on-1 / 一对一

Focus: smallest shape of all — and the most privacy-sensitive. Record
agreements and follow-ups, not a transcript of personal discussion.

```markdown
# 1:1 <A> × <B> — YYYY-MM-DD

## Agreed
- <what was agreed, by both>

## Follow-ups
| who | what | by-when |

## To raise next time
```

If the transcript contains personal matters, summarize the *outcome* only
("discussed workload; agreed to rebalance X") — minutes are not surveillance.

## Brainstorm / 头脑风暴

Focus: divergence is the product — ideas are **not** decisions, and grading
them in the minutes kills the next brainstorm.

```markdown
# Brainstorm: <topic> — YYYY-MM-DD
Participants: …

## Ideas (all of them, ungraded, deduplicated only)
- …

## Clusters / themes (if grouping emerged in the room)
## What happens next
- <who> will <shortlist / prototype / decide> by <when>

## Decisions
(usually empty — say so explicitly: "No decisions were made; that was the point.")
```

An empty Decisions bucket in a brainstorm is honest, not a failure.

## Action-item quality bar (SMART-ified who/what/by-when)

The skill's minimum is who / what / by-when. A *good* action item also passes
the SMART test (Specific, Measurable, Achievable, Relevant, Time-bound —
Doran, 1981); in minutes practice that means:

- **One owner, a person** — "frontend team will…" is a wish; "@Li will…" is a
  task. Two names = split it into two items or mark "owner unclear".
- **Verb + verifiable outcome** — "look into the bug" fails (when is it
  done?); "reproduce the login bug and post steps in the tracker" passes.
- **A real date** — "soon" / "next sprint" get flagged; "by-when: 07-11" is
  checkable at the next sync.
- **Stated in the room** — the recorder does not invent owners or dates to
  make an item look complete; a flagged incomplete item is more honest than a
  fabricated complete one.

Rewrite towards this bar **only from what was actually said**; anything you
had to add is an assumption and gets flagged.

## Decision record shape (who decided + basis)

Every decision entry carries, in one compact block:

```markdown
- **Decision**: <what was settled>
  - decided by: <name> (the accountable decider — if the room used
    DACI-style roles, this is the Driver/Approver; see Atlassian's DACI)
  - basis: <the reason given in the room — quote or close paraphrase, speaker named>
  - options considered: <only if actually discussed — do not reconstruct>
  - date: YYYY-MM-DD
```

No identifiable decider, or no stated basis? The entry moves to open
questions with exactly what is missing ("decision proposed, no owner").
"Everyone kind of nodded" is not a decision record.

## Glossary entry shape — worked examples

These are **fictional examples showing the shape**, not real terms — never
copy them into a user's glossary; entries get in only after the human
confirms an expansion:

| term | expansion / meaning | owner or source | added |
|------|---------------------|-----------------|-------|
| GTV | (example) gross transaction volume — finance's headline metric, weekly grain | (example) confirmed by Chen, finance | 2026-07-01 |
| 小火车 | (example) the internal nickname of the batch-import pipeline | (example) confirmed by Wang, backend | 2026-07-02 |
| P0 | (example) this team uses it for "drop everything today", *not* the bug-tracker priority | (example) confirmed by team lead | 2026-07-03 |

Note what the examples demonstrate: one line each; the expansion states the
*team's* meaning (which may differ from the industry's — see the P0 example);
the confirming human is named; the date makes stale terms visible.
