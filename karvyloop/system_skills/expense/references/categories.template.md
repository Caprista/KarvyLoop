# Expense category sheet — template (you own this)

This is the **human-owned** category sheet the `expense` skill checks before it
suggests a 科目 / category for a receipt — it is the expense domain's *semantic
layer*, the same idea as the meeting-notes glossary and the data-analyst's
term sheet. The product ships this *template*; your filled-in copy lives in
**your** space and is treated as **your data** — a data reset clears it, the
shipped template does not.

Why human-owned: which merchant belongs to which category, what your finance
team calls it, and what the per-category cap is are facts about **your company's
policy**, not something a model can infer. The skill never rules on
reimbursability and never invents a category — an unknown merchant comes back
as "category: unknown", and once you confirm a mapping it becomes a one-line
entry here. Watching this file grow from 0 to dozens of mappings is the honest
growth metric; the skill's suggestions are always a **hint you confirm**, never
a decision.

Keep entries one line each; add the date so stale rules are visible.

---

## Categories (科目)

| category / 科目 | typical merchants or items | per-item cap (if any) | notes | added |
|-----------------|----------------------------|-----------------------|-------|-------|
| <e.g. 餐饮 / Meals> | <e.g. 星巴克, 美团, restaurants> | <e.g. ¥150 / person> | <e.g. needs attendee list over ¥500> | <date> |
| <e.g. 交通 / Transport> | <e.g. 滴滴, 高铁, taxi> | | | |
| <e.g. 办公 / Office supplies> | <e.g. 京东, 得力> | | | |

## Merchant → category shortcuts (optional)

| merchant as it appears on receipts | category | tax id if known |
|------------------------------------|----------|-----------------|
| <e.g. 星巴克咖啡> | 餐饮 | |

## Policy reminders (optional — hints only, not rulings)

| rule of thumb | detail |
|---------------|--------|
| <e.g. invoices need the company payee 抬头> | <e.g. 公司全称 + 税号 must match> |
| <e.g. no reimbursement without 发票> | <e.g. 小票 alone insufficient over ¥100> |
