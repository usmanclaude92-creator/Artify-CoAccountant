# Accounting Periods (Phase 8)

* A company configures its fiscal-year start month and periods (`acct_periods`: fiscal year, name, start/end date, `open`/`closed`). `POST …/periods/generate {fiscal_year}` creates twelve monthly periods; custom periods can be created. Periods of one company cannot overlap.
* A transaction's date must fall in an existing **open** period (company setting `require_open_period`, default on). Otherwise validation is blocking (`period_closed`, `no_period`) at submission and approval. **The date is never changed to fit.**
* Closing needs `accounting.periods.manage` and is refused while a transaction dated in the period still awaits review or approval. Reopening needs the permission **and a written reason**. Every created/closed/reopened event is stored (`acct_period_events`) and audited.
* A reversal must itself be dated in an open period.
* Not implemented: year-end closing entries, retained-earnings transfer, period-lock exceptions per user.
