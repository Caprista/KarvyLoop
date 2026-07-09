---
name: expense
description: Read a receipt or invoice — pasted text, or a photo/scan — and turn it into one clean, structured expense record: merchant, date, currency, total, tax id, and itemised amounts, with those amounts checked to actually sum to the stated total. OCR text is dirty (O↔0, l↔1, misplaced decimals); this skill calibrates that using context but never invents a number it can't read. Recognition and structuring only — it does not decide what is reimbursable or file anything; the category is a hint you confirm. Image input needs the optional on-device OCR extra (or a vision-capable model); without either, it asks you to paste the text rather than guess. A system template — ships with the product, never deleted by a data reset.
version: "1.0"
signature: system:expense
source: system
scope: user
result_reuse: dynamic
when_to_use: When the user hands over a receipt, invoice (发票), shopping list, or itinerary — as pasted text or a photo/scan — and wants the key fields pulled out into a clean, checkable structured line (for a claim, a spreadsheet, or their records). 当用户丢来一张发票/小票/收据/购物清单/行程单(粘贴文字或拍照/扫描),想把商家、日期、金额、税号、明细抽成一条干净可核对的结构化记录时(用于报销、记账或存档)。
tags: [报销, 发票, 增值税发票, 小票, 收据, 票据, 购物清单, 消费清单, 行程单, 账单, 金额, 税号, 抬头, 明细, 报账, 贴票, expense, expenses, reimbursement, receipt, receipts, invoice, invoices, itinerary, ocr, scan]
allowed-tools:
  - read_file
  - reconcile_receipt
---

# Expense receipt reading (system template)

**A receipt is worth structuring only if the structure is trustworthy.** The
value here isn't "read some text off a picture" — it's turning a crumpled,
mis-scanned slip into **one line you can put in a claim without re-checking it
by hand**: right merchant, right date, right total, and — the part people skip —
line items that *actually add up to that total*. This skill is a **method**, not
a canned answer, and its scope is deliberately narrow: **recognition and
structuring, not judgment**. It does not decide what your company will
reimburse, does not submit anything, does not do your bookkeeping. It hands you
a clean, checked record and a category *hint* — you stay the one who decides.

## Honest input contract

This skill reads **text** or an **image**:

- **Text** — paste the receipt's text, or the export from a bill/e-invoice.
  Cleanest path, always works.
- **Image** — a photo or scan (jpg/png/…) is read via `read_file`, which OCRs
  it **on this machine** through the optional OCR extra
  (`pip install "karvyloop[ocr]"`; first use downloads a model, nothing is
  uploaded). If that extra isn't installed **and** the running model can't see
  images, the honest move is to **say so and ask you to paste the text** — never
  to invent a receipt from a filename. A vision-capable model may read the image
  directly instead; either way the same method below applies to the text.

## The accountability rule

I (atom) answer to the role; the role answers to you. A structured line with a
**wrong total or an invented tax id** is far worse than a field left blank and
flagged. Money and tax numbers are unforgiving. **When in doubt, leave it `null`
and flag it — never a confident fabrication.**

## Procedure — do not skip steps

1. **Identify what the document is.** receipt / invoice (发票, has a tax id and
   payee) / shopping list / itinerary / other. This decides which fields matter
   (an invoice's 税号 and 抬头; an itinerary's route and dates).

2. **Read once, then calibrate the OCR — from context, never by invention.**
   Raw OCR text is dirty. The usual damage and how to fix it *only when context
   makes it unambiguous*:
   - `O`↔`0`, `l`/`I`↔`1`, `S`↔`5`, `B`↔`8` inside numbers and dates
     (`2O26-O3-l5` → `2026-03-15`; `66.OO` → `66.00`);
   - a decimal point read in the wrong place — sanity-check against the other
     amounts and the total;
   - lines out of order — a receipt's layout is top-to-bottom, group by that.
   **Trust the OCR proportionally to what it tells you.** When the text is tagged
   with confidence marks like `合计⟦?0.42⟧`, the OCR was only that sure of that
   segment (0–1): **distrust the low ones first** — a `⟦?0.55⟧` number is a prime
   suspect, fix it from context or resolve it against the arithmetic (see step 4),
   and if you still can't pin it, leave `null` and flag it. Untagged text the OCR
   was confident about — but a confident OCR read can still be wrong, so a value
   that breaks the arithmetic is suspect even if untagged. If a character is
   genuinely unreadable, it stays unreadable: **calibrate the obvious, flag the
   ambiguous, invent nothing.**

3. **Extract the fields.** merchant, date, currency, total, tax id (`null` if
   not an invoice), payee/抬头, and line items as **name / qty / amount**, plus a
   category hint (step 5). Any field you can't read with confidence is `null`
   and named in the flags — a guessed amount is poison, a `null` is honest.

4. **Check the arithmetic — call `reconcile_receipt`, don't do it in your head.**
   The arithmetic is deterministic, so it must not depend on you (the model)
   adding correctly. Call the **`reconcile_receipt` tool** with what you
   extracted — `line_items` (name / qty / unit_price / amount), `subtotal`,
   `tax`, `total` — **leaving every low-confidence or unreadable number as
   `null`** (don't pass a shaky reading down; the tool recovers it from the
   receipt's own math). It reverse-solves values the arithmetic pins down
   (`unit_price×qty=amount`, `Σamounts=subtotal`, `subtotal+tax=total`), flags
   what it can't determine, and never guesses. **Use its returned numbers and
   `flags` as the source of truth over your own mental arithmetic**, and surface
   its `flags` (e.g. `sum_mismatch`, unresolved line amounts) to the user. If the
   tool is unavailable, fall back to summing by hand — but flag that it was not
   independently verified.

5. **Suggest a category — as a hint, from the company sheet, never as a ruling.**
   Check the merchant / items against the team's expense-category sheet (the
   filled-in copy of `references/categories.template.md`, **human-owned**, in
   your space — it's this domain's semantic layer: which merchants map to which
   科目, per-category caps, what your finance team calls things). A match →
   propose it, marked "hint, please confirm". No match → say "category:
   unknown", don't guess a 科目. **Whether it's reimbursable at all is your
   call, not mine.**

6. **Output one clean structured record.** Default shape: a compact block /
   table — doc type, merchant, date, currency, **total**, tax id + payee (if an
   invoice), a line-item table (name | qty | amount), the arithmetic verdict,
   the category hint, and a **flags** line listing every `null` and every
   mismatch. If the user wants JSON or a specific column order for their
   spreadsheet, theirs wins.

## What crystallizes

The reusable asset is this **method** (identify → calibrate → extract → *check
the sum* → hint, never invent) plus, over repeated use, your company's growing
**category sheet** — from 0 to dozens of merchant→科目 mappings and caps, a file
you can watch get longer and hand a new hire on day one. Confirmed mappings come
back as candidates for that sheet; I never write canned rules into it myself.

## When you cannot proceed

Photo too blurred or skewed for OCR to read amounts? Say so, produce whatever
fields are legible with the rest flagged `null`, and ask for a clearer shot or
the pasted text — **do not guess the total.** No OCR extra and a text-only
model? Decline the image honestly and point to
`pip install "karvyloop[ocr]"` or ask for the text. Line items that refuse to
sum to the total after honest calibration? Report both numbers and the
`sum_mismatch` flag — a receipt that doesn't add up is a fact the person needs,
not a problem to paper over.
