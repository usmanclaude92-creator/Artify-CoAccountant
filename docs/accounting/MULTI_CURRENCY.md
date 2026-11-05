# Multi-Currency (Phase 8)

* The company has a **base currency** (fixed once accounting is set up) and a precision (`amount_scale`, default the currency's minor unit).
* A transaction has its own currency. Journal lines hold amounts in the transaction currency **and** the base currency.
* **Rates are never invented.** `acct_currency_rates` holds rates the company entered or loaded from a configured source (`source` is recorded; existing rates are never overwritten). When a transaction has no explicit rate the most recent rate dated on or before the transaction date is used; none → blocking `missing_exchange_rate`. A rate older than `max_rate_age_days` (default 7) gives a warning with the dates.
* The rate, its source and its date are copied onto the transaction and the journal, so historical conversions are reproducible even if rates change later.
* Base amounts = amount × rate, rounded to the base scale with the company's rounding mode. A residual rounding difference within tolerance is posted to the company's **rounding** role account and shown as a warning; without that account (or beyond tolerance) the journal stays unbalanced and blocked.
* Foreign-currency transactions need the plan flag `feature.multi_currency`; existing data is never touched when a plan changes.
* **Not implemented:** unrealised/realised FX gain or loss, revaluation, multi-currency bank accounts as such, triangulation.
