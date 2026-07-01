# Semantic layer — template (you own this)

This is the **human-owned** definition layer the `data-analyst` skill anchors to.
The product ships this *template*; your filled-in copy lives in **your** data
space (`~/.karvyloop/data/<dataset>/semantic-layer.md`) and is treated as **your
data** — a data reset clears it, the shipped template does not. Auto-generating
these definitions is net-negative; write them yourself (or confirm them), because
they are the meaning a query cannot infer.

Fill one block per table. Keep it small and true; grow it as questions arise.

---

## dataset: <name>

**Source:** <db connection / file path / warehouse schema>
**Freshness:** <how often it updates; how to tell if stale>
**Grain:** <one row = ? e.g. "one order line", "one user-day">

### table: <table_name>

| column | meaning | type | notes / caveats |
|--------|---------|------|-----------------|
| <col>  | <what it actually represents> | <type> | <gotchas, units, nullability> |

### metrics (canonical definitions — the source of truth)

- **<metric name>**: <exact definition, including filters and grain>.
  - Formula: <how it is computed from columns>
  - Default filters: <e.g. exclude test accounts, status = 'paid'>
  - Known traps: <e.g. "do not SUM across days — it double-counts">

### entity disambiguation (the #1 failure mode)

- "<ambiguous term users say>" → <which concrete definition / column to use>.

---

## offline tests (question → known answer)

Keep a handful of question→answer pairs you trust. The skill runs new queries
against these before trusting a result; a query that breaks one is wrong until
proven otherwise.

| question | expected answer | as of | how it was verified |
|----------|-----------------|-------|---------------------|
| <q>      | <a>             | <date>| <by whom / how>     |
