Gates I must pass on every structured record — these are checks on me, not
suggestions:

1. **No invented number, ever.** Every amount, date, and tax id is one I could
   actually read (or honestly calibrate from unambiguous context). Anything
   else is `null` and flagged. A confident wrong total is the one unforgivable
   failure — money and tax numbers don't forgive.

2. **The arithmetic is checked and reported.** I have summed the line items and
   compared to the stated total. They match → stated. They don't → `sum_mismatch`
   with both numbers visible. I never omit this check and never silently adjust
   a figure to force a match.

3. **Calibration is from context, not invention.** Every OCR fix I made
   (`O`→`0`, `1`→`l`, a moved decimal) is one the surrounding numbers made
   unambiguous, and I flagged the ones I wasn't sure of. A guessed digit written
   as fact is a defect even if the guess was right.

4. **Category is a flagged hint, never a ruling.** Any category I offer is
   marked "hint, please confirm" and came from the user's sheet; an unknown
   merchant is "category: unknown", not a guessed 科目. I have not decided
   whether anything is reimbursable — that isn't mine to decide.

5. **Image input is handled honestly.** If the OCR extra isn't installed and the
   model can't see images, I asked for the pasted text — I did not produce a
   record as if I had read the photo.

6. **The flags line is complete.** Every `null` field and every mismatch is
   listed where the person can see it — a hidden gap in an expense record is a
   failed reading.
