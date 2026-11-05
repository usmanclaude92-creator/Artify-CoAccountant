-- Phase 8: accounting intelligence engine.
-- Company-owned accounting configuration, chart of accounts, tax, rules, transactions, double-entry journals and the
-- approval trail. Every amount is NUMERIC (never float). Approved records are immutable (triggers below): corrections
-- are reversals or amendments, never edits. Approval requires a user (CHECKs + trigger), never the system or AI.

-- ------------------------------------------------------------------ configuration (independent per company)
CREATE TABLE acct_configurations (
  id                      uuid PRIMARY KEY,
  organization_id         uuid NOT NULL,
  company_id              uuid NOT NULL,
  base_currency           char(3) NOT NULL REFERENCES currencies (code),
  amount_scale            smallint NOT NULL CHECK (amount_scale BETWEEN 0 AND 6),
  fiscal_year_start_month smallint NOT NULL DEFAULT 1 CHECK (fiscal_year_start_month BETWEEN 1 AND 12),
  accounting_method       text NOT NULL DEFAULT 'accrual' CHECK (accounting_method IN ('accrual', 'cash')),
  rounding_mode           text NOT NULL DEFAULT 'half_up' CHECK (rounding_mode IN ('half_up', 'half_even')),
  tax_rounding_scope      text NOT NULL DEFAULT 'line' CHECK (tax_rounding_scope IN ('line', 'document')),
  tolerance               numeric(24, 6) NOT NULL DEFAULT 0.01 CHECK (tolerance >= 0),
  jurisdiction_code       text CHECK (jurisdiction_code IS NULL OR jurisdiction_code ~ '^[A-Z0-9_]{2,16}$'),
  tax_registered          boolean NOT NULL DEFAULT false,
  tax_registration_number text CHECK (tax_registration_number IS NULL OR length(tax_registration_number) <= 40),
  require_open_period     boolean NOT NULL DEFAULT true,
  required_dimensions     text[] NOT NULL DEFAULT '{}' CHECK (required_dimensions <@ ARRAY['project', 'cost_center', 'department']),
  max_rate_age_days       integer NOT NULL DEFAULT 7 CHECK (max_rate_age_days BETWEEN 0 AND 365),
  numbering               jsonb NOT NULL DEFAULT '{"transaction": {"prefix": "TX-", "padding": 6}, "journal": {"prefix": "JV-", "padding": 6}}'::jsonb,
  created_by              uuid REFERENCES users (id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_by              uuid REFERENCES users (id),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  row_version             integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (company_id),
  UNIQUE (organization_id, company_id, id)
);

CREATE TABLE acct_accounts (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  code            text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$'),
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 160),
  parent_id       uuid,
  account_type    text NOT NULL CHECK (account_type IN ('asset', 'current_asset', 'non_current_asset', 'liability', 'current_liability',
                    'long_term_liability', 'equity', 'revenue', 'other_income', 'cost_of_sales', 'expense', 'other_expense', 'tax',
                    'bank', 'cash', 'receivable', 'payable')),
  normal_balance  text NOT NULL CHECK (normal_balance IN ('debit', 'credit')),
  currency        char(3) REFERENCES currencies (code),
  is_postable     boolean NOT NULL DEFAULT true,
  is_active       boolean NOT NULL DEFAULT true,
  description     text CHECK (description IS NULL OR length(description) <= 500),
  external_code   text CHECK (external_code IS NULL OR length(external_code) <= 64),
  archived_at     timestamptz,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      uuid REFERENCES users (id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  row_version     integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, parent_id) REFERENCES acct_accounts (organization_id, company_id, id),
  UNIQUE (company_id, code),
  UNIQUE (organization_id, company_id, id),
  CHECK (parent_id IS NULL OR parent_id <> id)
);
CREATE INDEX acct_accounts_by_parent ON acct_accounts (company_id, parent_id);

CREATE TABLE acct_role_mappings (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  role            text NOT NULL CHECK (role IN ('ar_control', 'ap_control', 'employee_payable', 'vat_input', 'vat_output', 'bank', 'cash',
                    'bank_charges', 'expense_default', 'revenue_default', 'suspense', 'rounding')),
  account_id      uuid NOT NULL,
  updated_by      uuid REFERENCES users (id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  UNIQUE (company_id, role)
);

-- ------------------------------------------------------------------ periods
CREATE TABLE acct_periods (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  fiscal_year     integer NOT NULL CHECK (fiscal_year BETWEEN 1900 AND 2200),
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 60),
  start_date      date NOT NULL,
  end_date        date NOT NULL,
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  closed_by       uuid REFERENCES users (id),
  closed_at       timestamptz,
  reopened_by     uuid REFERENCES users (id),
  reopened_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (company_id, start_date),
  UNIQUE (organization_id, company_id, id),
  CHECK (start_date <= end_date)
);

CREATE TABLE acct_period_events (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  period_id       uuid NOT NULL,
  action          text NOT NULL CHECK (action IN ('created', 'closed', 'reopened')),
  actor_id        uuid NOT NULL REFERENCES users (id),
  reason          text CHECK (reason IS NULL OR length(reason) <= 500),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, period_id) REFERENCES acct_periods (organization_id, company_id, id)
);

-- ------------------------------------------------------------------ tax (configuration, never calculation)
CREATE TABLE acct_tax_codes (
  id                  uuid PRIMARY KEY,
  organization_id     uuid NOT NULL,
  company_id          uuid NOT NULL,
  code                text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,23}$'),
  name                text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  treatment           text NOT NULL CHECK (treatment IN ('standard', 'zero_rated', 'exempt', 'out_of_scope', 'reverse_charge', 'import')),
  direction           text NOT NULL DEFAULT 'both' CHECK (direction IN ('input', 'output', 'both')),
  input_account_id    uuid,
  output_account_id   uuid,
  recoverable         boolean NOT NULL DEFAULT true,
  is_active           boolean NOT NULL DEFAULT true,
  jurisdiction_code   text CHECK (jurisdiction_code IS NULL OR jurisdiction_code ~ '^[A-Z0-9_]{2,16}$'),
  source_reference    text CHECK (source_reference IS NULL OR length(source_reference) <= 400),
  verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified', 'verified')),
  verified_on         date,
  verified_by_name    text CHECK (verified_by_name IS NULL OR length(verified_by_name) <= 120),
  created_by          uuid REFERENCES users (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, input_account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, output_account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  UNIQUE (company_id, code),
  UNIQUE (organization_id, company_id, id),
  CHECK (verification_status <> 'verified' OR (verified_on IS NOT NULL AND verified_by_name IS NOT NULL))
);

CREATE TABLE acct_tax_rates (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  tax_code_id     uuid NOT NULL,
  rate            numeric(9, 4) NOT NULL CHECK (rate >= 0 AND rate <= 100),
  effective_from  date NOT NULL,
  effective_to    date,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, tax_code_id) REFERENCES acct_tax_codes (organization_id, company_id, id),
  UNIQUE (tax_code_id, effective_from),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

-- ------------------------------------------------------------------ currencies: rates are entered or configured, never invented
CREATE TABLE acct_currency_rates (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  currency        char(3) NOT NULL REFERENCES currencies (code),
  base_currency   char(3) NOT NULL REFERENCES currencies (code),
  rate            numeric(20, 10) NOT NULL CHECK (rate > 0),  -- base units per ONE unit of `currency`
  rate_date       date NOT NULL,
  source          text NOT NULL CHECK (length(btrim(source)) BETWEEN 1 AND 80),
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (company_id, currency, rate_date, source),
  CHECK (currency <> base_currency)
);

-- ------------------------------------------------------------------ customers and suppliers
CREATE TABLE acct_contacts (
  id                    uuid PRIMARY KEY,
  organization_id       uuid NOT NULL,
  company_id            uuid NOT NULL,
  kind                  text NOT NULL CHECK (kind IN ('customer', 'supplier')),
  legal_name            text NOT NULL CHECK (length(btrim(legal_name)) BETWEEN 1 AND 200),
  display_name          text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
  tax_number            text CHECK (tax_number IS NULL OR length(tax_number) <= 40),
  email                 text CHECK (email IS NULL OR length(email) <= 200),
  phone                 text CHECK (phone IS NULL OR length(phone) <= 40),
  address               text CHECK (address IS NULL OR length(address) <= 500),
  currency              char(3) REFERENCES currencies (code),
  receivable_account_id uuid,
  payable_account_id    uuid,
  default_tax_code_id   uuid,
  payment_terms_days    integer CHECK (payment_terms_days IS NULL OR payment_terms_days BETWEEN 0 AND 3650),
  is_active             boolean NOT NULL DEFAULT true,
  external_ref          text CHECK (external_ref IS NULL OR length(external_ref) <= 80),
  created_by            uuid REFERENCES users (id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_by            uuid REFERENCES users (id),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  row_version           integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, receivable_account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, payable_account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, default_tax_code_id) REFERENCES acct_tax_codes (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE UNIQUE INDEX acct_contacts_tax_number ON acct_contacts (company_id, kind, upper(regexp_replace(tax_number, '[^0-9A-Za-z]', '', 'g')))
  WHERE tax_number IS NOT NULL AND btrim(tax_number) <> '';
CREATE INDEX acct_contacts_by_name ON acct_contacts (company_id, kind, lower(display_name));

-- ------------------------------------------------------------------ rules (data, versioned; no scripting)
CREATE TABLE acct_rules (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  rule_key        uuid NOT NULL,
  version         integer NOT NULL CHECK (version >= 1),
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  kind            text NOT NULL CHECK (kind IN ('account', 'tax', 'narration', 'dimension')),
  priority        integer NOT NULL DEFAULT 0 CHECK (priority BETWEEN -1000 AND 1000),
  conditions      jsonb NOT NULL CHECK (jsonb_typeof(conditions) = 'array' AND jsonb_array_length(conditions) BETWEEN 1 AND 10),
  action          jsonb NOT NULL CHECK (jsonb_typeof(action) = 'object'),
  effective_from  date,
  effective_to    date,
  is_active       boolean NOT NULL DEFAULT true,
  is_current      boolean NOT NULL DEFAULT true,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (rule_key, version),
  UNIQUE (organization_id, company_id, id),
  CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);
CREATE UNIQUE INDEX acct_rules_one_current ON acct_rules (rule_key) WHERE is_current;
CREATE INDEX acct_rules_by_company ON acct_rules (company_id, kind) WHERE is_current;

-- ------------------------------------------------------------------ approval policy and counters
CREATE TABLE acct_approval_policies (
  id                        uuid PRIMARY KEY,
  organization_id           uuid NOT NULL,
  company_id                uuid NOT NULL,
  separate_preparer_approver boolean NOT NULL DEFAULT true,
  levels                    smallint NOT NULL DEFAULT 1 CHECK (levels IN (1, 2)),
  distinct_levels           boolean NOT NULL DEFAULT true,
  approval_limit            numeric(24, 6) CHECK (approval_limit IS NULL OR approval_limit >= 0),
  updated_by                uuid REFERENCES users (id),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (company_id)
);

CREATE TABLE acct_counters (
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  key             text NOT NULL CHECK (key IN ('transaction', 'journal')),
  value           bigint NOT NULL DEFAULT 0,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  PRIMARY KEY (company_id, key)
);

-- ------------------------------------------------------------------ transactions
CREATE TABLE acct_transactions (
  id                  uuid PRIMARY KEY,
  organization_id     uuid NOT NULL,
  company_id          uuid NOT NULL,
  number              text NOT NULL,
  txn_type            text NOT NULL CHECK (txn_type IN ('sales_invoice', 'sales_credit_note', 'customer_receipt', 'customer_refund',
                        'purchase_invoice', 'purchase_credit_note', 'supplier_payment', 'supplier_refund', 'cash_expense', 'bank_expense',
                        'employee_expense', 'general_expense', 'bank_deposit', 'bank_withdrawal', 'bank_charges', 'bank_transfer',
                        'journal_entry', 'opening_balance', 'adjustment_entry', 'accrual', 'prepayment', 'depreciation', 'reversal_entry')),
  status              text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'awaiting_review', 'awaiting_approval', 'approved', 'rejected', 'posted', 'reversed')),
  source_document_id  uuid,
  extraction_id       uuid,
  source_ref          uuid,           -- e.g. the bank statement row this transaction came from
  source_key          text NOT NULL DEFAULT 'doc',
  document_checksum   text,
  transaction_date    date NOT NULL,
  due_date            date,
  doc_number          text CHECK (doc_number IS NULL OR length(doc_number) <= 80),
  contact_id          uuid,
  contact_suggestions jsonb NOT NULL DEFAULT '[]'::jsonb,
  settlement_account_id uuid,
  counter_account_id  uuid,
  currency            char(3) NOT NULL REFERENCES currencies (code),
  exchange_rate       numeric(20, 10) CHECK (exchange_rate IS NULL OR exchange_rate > 0),
  rate_source         text,
  rate_date           date,
  prices_include_tax  boolean NOT NULL DEFAULT false,
  net_amount          numeric(24, 6) NOT NULL DEFAULT 0 CHECK (net_amount >= 0),
  tax_amount          numeric(24, 6) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
  gross_amount        numeric(24, 6) NOT NULL DEFAULT 0 CHECK (gross_amount >= 0),
  stated_amounts      jsonb NOT NULL DEFAULT '{}'::jsonb,   -- the totals printed on the document, as extracted (never altered)
  base_gross_amount   numeric(24, 6),
  narration           text CHECK (narration IS NULL OR length(narration) <= 500),
  journal_mode        text NOT NULL DEFAULT 'generated' CHECK (journal_mode IN ('generated', 'manual')),
  validation          jsonb NOT NULL DEFAULT '[]'::jsonb,
  validated_at        timestamptz,
  override_note       text CHECK (override_note IS NULL OR length(override_note) <= 500),
  duplicate_status    text NOT NULL DEFAULT 'none' CHECK (duplicate_status IN ('none', 'possible', 'exact')),
  correction_note     text CHECK (correction_note IS NULL OR length(correction_note) <= 500),
  prepared_by         uuid REFERENCES users (id),
  submitted_by        uuid REFERENCES users (id),
  submitted_at        timestamptz,
  reviewed_by         uuid REFERENCES users (id),
  reviewed_at         timestamptz,
  approved_by         uuid REFERENCES users (id),
  approved_at         timestamptz,
  rejected_by         uuid REFERENCES users (id),
  rejected_at         timestamptz,
  rejection_reason    text CHECK (rejection_reason IS NULL OR length(rejection_reason) <= 500),
  reversal_of_id      uuid,
  reversed_by         uuid REFERENCES users (id),
  reversed_at         timestamptz,
  amends_id           uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  row_version         integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, source_document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, extraction_id) REFERENCES extractions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, contact_id) REFERENCES acct_contacts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, settlement_account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, counter_account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, reversal_of_id) REFERENCES acct_transactions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, amends_id) REFERENCES acct_transactions (organization_id, company_id, id),
  UNIQUE (company_id, number),
  UNIQUE (organization_id, company_id, id),
  -- an approved record names the person who approved it (the system and AI can never be that person)
  CHECK (status NOT IN ('approved', 'posted', 'reversed') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CHECK (status <> 'rejected' OR (rejected_by IS NOT NULL AND rejection_reason IS NOT NULL)),
  CHECK (status <> 'reversed' OR (reversed_by IS NOT NULL AND reversed_at IS NOT NULL))
);
-- one live transaction per extraction (and bank row): regenerating is refused until the earlier one is rejected
CREATE UNIQUE INDEX acct_transactions_one_per_source ON acct_transactions (extraction_id, source_key)
  WHERE extraction_id IS NOT NULL AND status <> 'rejected' AND reversal_of_id IS NULL AND amends_id IS NULL;
CREATE INDEX acct_transactions_by_company ON acct_transactions (company_id, status, transaction_date DESC);
CREATE INDEX acct_transactions_by_contact ON acct_transactions (company_id, contact_id);
CREATE INDEX acct_transactions_by_document ON acct_transactions (source_document_id);
CREATE INDEX acct_transactions_dup ON acct_transactions (company_id, lower(doc_number), gross_amount);

CREATE TABLE acct_transaction_lines (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  transaction_id  uuid NOT NULL,
  line_no         integer NOT NULL CHECK (line_no >= 1),
  description     text CHECK (description IS NULL OR length(description) <= 500),
  account_id      uuid,
  account_source  text NOT NULL DEFAULT 'manual' CHECK (account_source IN ('manual', 'rule', 'contact', 'role_default', 'unresolved')),
  entry_side      text CHECK (entry_side IS NULL OR entry_side IN ('debit', 'credit')),
  quantity        numeric(20, 6) CHECK (quantity IS NULL OR quantity >= 0),
  unit_price      numeric(24, 6) CHECK (unit_price IS NULL OR unit_price >= 0),
  discount        numeric(24, 6) NOT NULL DEFAULT 0 CHECK (discount >= 0),
  net_amount      numeric(24, 6) NOT NULL DEFAULT 0 CHECK (net_amount >= 0),
  tax_amount      numeric(24, 6) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
  gross_amount    numeric(24, 6) NOT NULL DEFAULT 0 CHECK (gross_amount >= 0),
  tax_code_id     uuid,
  dimensions      jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(dimensions) = 'object'),
  source_ref      jsonb NOT NULL DEFAULT '{}'::jsonb,
  calc            jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, transaction_id) REFERENCES acct_transactions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, tax_code_id) REFERENCES acct_tax_codes (organization_id, company_id, id),
  UNIQUE (transaction_id, line_no)
);

-- ------------------------------------------------------------------ journals (double entry)
CREATE TABLE acct_journals (
  id                 uuid PRIMARY KEY,
  organization_id    uuid NOT NULL,
  company_id         uuid NOT NULL,
  number             text,
  journal_type       text NOT NULL CHECK (journal_type IN ('general', 'sales', 'purchases', 'bank', 'cash', 'adjustment', 'opening', 'reversal')),
  journal_date       date NOT NULL,
  period_id          uuid,
  narration          text CHECK (narration IS NULL OR length(narration) <= 500),
  transaction_id     uuid NOT NULL,
  source_document_id uuid,
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'posted', 'reversed')),
  currency           char(3) NOT NULL REFERENCES currencies (code),
  base_currency      char(3) NOT NULL REFERENCES currencies (code),
  exchange_rate      numeric(20, 10) CHECK (exchange_rate IS NULL OR exchange_rate > 0),
  rate_source        text,
  rate_date          date,
  total_debit        numeric(24, 6) NOT NULL DEFAULT 0,
  total_credit       numeric(24, 6) NOT NULL DEFAULT 0,
  base_total_debit   numeric(24, 6) NOT NULL DEFAULT 0,
  base_total_credit  numeric(24, 6) NOT NULL DEFAULT 0,
  created_by         uuid REFERENCES users (id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  approved_by        uuid REFERENCES users (id),
  approved_at        timestamptz,
  reversal_of_id     uuid,
  updated_at         timestamptz NOT NULL DEFAULT now(),
  row_version        integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, period_id) REFERENCES acct_periods (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, transaction_id) REFERENCES acct_transactions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, source_document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, reversal_of_id) REFERENCES acct_journals (organization_id, company_id, id),
  UNIQUE (transaction_id),                               -- one journal per transaction: duplicate posting is impossible
  UNIQUE (company_id, number),
  UNIQUE (organization_id, company_id, id),
  CHECK (status = 'draft' OR (number IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE INDEX acct_journals_by_company ON acct_journals (company_id, status, journal_date);

CREATE TABLE acct_journal_lines (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  journal_id      uuid NOT NULL,
  line_no         integer NOT NULL CHECK (line_no >= 1),
  account_id      uuid NOT NULL,
  debit           numeric(24, 6) NOT NULL DEFAULT 0 CHECK (debit >= 0),
  credit          numeric(24, 6) NOT NULL DEFAULT 0 CHECK (credit >= 0),
  base_debit      numeric(24, 6) NOT NULL DEFAULT 0 CHECK (base_debit >= 0),
  base_credit     numeric(24, 6) NOT NULL DEFAULT 0 CHECK (base_credit >= 0),
  currency        char(3) NOT NULL REFERENCES currencies (code),
  description     text CHECK (description IS NULL OR length(description) <= 500),
  reference       text CHECK (reference IS NULL OR length(reference) <= 120),
  tax_code_id     uuid,
  contact_id      uuid,
  dimensions      jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(dimensions) = 'object'),
  source          text NOT NULL DEFAULT 'generated' CHECK (source IN ('generated', 'manual', 'rounding')),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, journal_id) REFERENCES acct_journals (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, account_id) REFERENCES acct_accounts (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, tax_code_id) REFERENCES acct_tax_codes (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, contact_id) REFERENCES acct_contacts (organization_id, company_id, id),
  UNIQUE (journal_id, line_no),
  CHECK (NOT (debit > 0 AND credit > 0)),
  CHECK (NOT (base_debit > 0 AND base_credit > 0))
);
CREATE INDEX acct_journal_lines_by_account ON acct_journal_lines (company_id, account_id);
CREATE INDEX acct_journal_lines_by_contact ON acct_journal_lines (company_id, contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX acct_journal_lines_by_tax ON acct_journal_lines (company_id, tax_code_id) WHERE tax_code_id IS NOT NULL;

-- ------------------------------------------------------------------ approvals (append-only trail) and duplicates
CREATE TABLE acct_approvals (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  transaction_id  uuid NOT NULL,
  action          text NOT NULL CHECK (action IN ('create', 'submit', 'review_approve', 'approve', 'reject', 'request_correction',
                    'revise', 'reverse', 'amend', 'override')),
  from_status     text,
  to_status       text,
  actor_type      text NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user', 'system', 'ai')),
  actor_id        uuid REFERENCES users (id),
  note            text CHECK (note IS NULL OR length(note) <= 500),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, transaction_id) REFERENCES acct_transactions (organization_id, company_id, id),
  -- structural: only a person can approve, review-approve or reverse
  CHECK (action NOT IN ('approve', 'review_approve', 'reverse') OR (actor_type = 'user' AND actor_id IS NOT NULL))
);
CREATE INDEX acct_approvals_by_transaction ON acct_approvals (transaction_id, created_at);

CREATE TABLE acct_duplicate_matches (
  id                     uuid PRIMARY KEY,
  organization_id        uuid NOT NULL,
  company_id             uuid NOT NULL,
  transaction_id         uuid NOT NULL,
  matched_transaction_id uuid NOT NULL,
  level                  text NOT NULL CHECK (level IN ('exact', 'possible')),
  signals                jsonb NOT NULL DEFAULT '{}'::jsonb,
  decision               text CHECK (decision IN ('not_duplicate', 'duplicate')),
  decided_by             uuid REFERENCES users (id),
  decided_at             timestamptz,
  note                   text CHECK (note IS NULL OR length(note) <= 500),
  created_at             timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, transaction_id) REFERENCES acct_transactions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, matched_transaction_id) REFERENCES acct_transactions (organization_id, company_id, id),
  UNIQUE (transaction_id, matched_transaction_id),
  CHECK (transaction_id <> matched_transaction_id),
  CHECK (decision IS NULL OR (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);

-- ------------------------------------------------------------------ immutability and integrity triggers
CREATE FUNCTION acct_guard_journal_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  SELECT status INTO st FROM acct_journals WHERE id = COALESCE(NEW.journal_id, OLD.journal_id);
  IF st IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'lines of a % journal cannot be changed; reverse it instead', COALESCE(st, 'missing') USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER acct_journal_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON acct_journal_lines
  FOR EACH ROW EXECUTE FUNCTION acct_guard_journal_line();

CREATE FUNCTION acct_guard_journal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE n integer; d numeric; c numeric; bd numeric; bc numeric;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'an approved journal cannot be deleted' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' THEN RAISE EXCEPTION 'journals are created as drafts' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.status <> 'draft' THEN
    IF (to_jsonb(NEW) - 'status' - 'updated_at' - 'row_version') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at' - 'row_version') THEN
      RAISE EXCEPTION 'an approved journal is immutable; reverse it instead' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT ((OLD.status = 'approved' AND NEW.status IN ('approved', 'posted', 'reversed'))
         OR (OLD.status = 'posted' AND NEW.status IN ('posted', 'reversed'))
         OR (OLD.status = 'reversed' AND NEW.status = 'reversed')) THEN
      RAISE EXCEPTION 'invalid journal status change % -> %', OLD.status, NEW.status USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  ELSIF NEW.status <> 'draft' THEN
    -- approval: the journal must balance (transaction and base currency) and have at least two lines
    SELECT count(*), COALESCE(sum(debit), 0), COALESCE(sum(credit), 0), COALESCE(sum(base_debit), 0), COALESCE(sum(base_credit), 0)
      INTO n, d, c, bd, bc FROM acct_journal_lines WHERE journal_id = NEW.id;
    IF n < 2 THEN RAISE EXCEPTION 'a journal needs at least two lines' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    IF d <> c OR bd <> bc THEN
      RAISE EXCEPTION 'journal is not balanced (debit %, credit %; base debit %, base credit %)', d, c, bd, bc USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.total_debit := d; NEW.total_credit := c; NEW.base_total_debit := bd; NEW.base_total_credit := bc;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER acct_journals_guard BEFORE INSERT OR UPDATE OR DELETE ON acct_journals
  FOR EACH ROW EXECUTE FUNCTION acct_guard_journal();

CREATE FUNCTION acct_guard_transaction() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE jst text; sep boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'accounting transactions are never deleted' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' THEN RAISE EXCEPTION 'transactions start as drafts' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.status IN ('approved', 'posted', 'reversed') THEN
    IF (to_jsonb(NEW) - 'status' - 'updated_at' - 'row_version' - 'reversed_by' - 'reversed_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at' - 'row_version' - 'reversed_by' - 'reversed_at') THEN
      RAISE EXCEPTION 'an approved transaction is immutable; reverse or amend it instead' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT ((OLD.status = 'approved' AND NEW.status IN ('approved', 'posted', 'reversed'))
         OR (OLD.status = 'posted' AND NEW.status IN ('posted', 'reversed'))
         OR (OLD.status = 'reversed' AND NEW.status = 'reversed')) THEN
      RAISE EXCEPTION 'invalid transaction status change % -> %', OLD.status, NEW.status USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status = 'approved' THEN
    -- approving needs an approved, balanced journal and (when the policy says so) a different person from the preparer
    SELECT status INTO jst FROM acct_journals WHERE transaction_id = NEW.id;
    IF jst IS NULL OR jst NOT IN ('approved', 'posted') THEN
      RAISE EXCEPTION 'the journal must be approved (and balanced) before the transaction' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT separate_preparer_approver INTO sep FROM acct_approval_policies WHERE company_id = NEW.company_id;
    IF COALESCE(sep, true) AND NEW.reversal_of_id IS NULL AND NEW.approved_by = NEW.prepared_by THEN
      RAISE EXCEPTION 'the approver must be a different person from the preparer' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER acct_transactions_guard BEFORE INSERT OR UPDATE OR DELETE ON acct_transactions
  FOR EACH ROW EXECUTE FUNCTION acct_guard_transaction();

-- lines of a transaction can only change while it is a draft
CREATE FUNCTION acct_guard_transaction_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  SELECT status INTO st FROM acct_transactions WHERE id = COALESCE(NEW.transaction_id, OLD.transaction_id);
  IF st IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'lines of a % transaction cannot be changed', COALESCE(st, 'missing') USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER acct_transaction_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON acct_transaction_lines
  FOR EACH ROW EXECUTE FUNCTION acct_guard_transaction_line();

-- ------------------------------------------------------------------ row-level security (same pattern as ADR-024)
DO $$
DECLARE
  t text;
  scope text := 'organization_id = app.org_id() AND (app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
BEGIN
  FOREACH t IN ARRAY ARRAY['acct_configurations', 'acct_accounts', 'acct_role_mappings', 'acct_periods', 'acct_period_events', 'acct_tax_codes',
                           'acct_tax_rates', 'acct_currency_rates', 'acct_contacts', 'acct_rules', 'acct_approval_policies', 'acct_counters',
                           'acct_transactions', 'acct_transaction_lines', 'acct_journals', 'acct_journal_lines', 'acct_approvals',
                           'acct_duplicate_matches'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO artify_app USING (%s)', t || '_select', t, scope);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT TO artify_app WITH CHECK (%s)', t || '_insert', t, scope);
    IF t NOT IN ('acct_approvals', 'acct_period_events') THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', t || '_update', t, scope, scope);
    END IF;
    IF t IN ('acct_transaction_lines', 'acct_journal_lines', 'acct_role_mappings', 'acct_tax_rates') THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR DELETE TO artify_app USING (%s)', t || '_delete', t, scope);
    END IF;
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON acct_configurations, acct_accounts, acct_periods, acct_tax_codes, acct_currency_rates, acct_contacts,
  acct_rules, acct_approval_policies, acct_counters, acct_transactions, acct_journals, acct_duplicate_matches TO artify_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON acct_role_mappings, acct_tax_rates, acct_transaction_lines, acct_journal_lines TO artify_app;
GRANT SELECT, INSERT ON acct_period_events, acct_approvals TO artify_app;   -- append-only trails
-- no DELETE is granted on accounts, contacts, transactions, journals, rules or periods: archive, deactivate or reverse instead

-- organization-wide accounting quotas are measured by the narrow definer function
GRANT SELECT ON acct_transactions, acct_journals TO artify_definer;
CREATE POLICY definer_read ON acct_transactions FOR SELECT TO artify_definer USING (organization_id = app.org_id());
CREATE POLICY definer_read ON acct_journals FOR SELECT TO artify_definer USING (organization_id = app.org_id());

-- entitlement measurement: Phase 7 branches plus the accounting quotas
CREATE OR REPLACE FUNCTION app.measure_entitlement(p_org uuid, p_key text) RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v bigint;
BEGIN
  IF p_org IS DISTINCT FROM app.org_id() THEN
    RAISE EXCEPTION 'organization context mismatch' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_key = 'limit.companies' THEN
    SELECT count(*) INTO v FROM companies WHERE organization_id = p_org AND status <> 'archived';
  ELSIF p_key = 'limit.users' THEN
    SELECT count(DISTINCT u) INTO v FROM (
      SELECT user_id AS u FROM organization_memberships WHERE organization_id = p_org AND status = 'active'
      UNION
      SELECT user_id FROM company_memberships WHERE organization_id = p_org AND status = 'active') x;
  ELSIF p_key = 'limit.accountants' THEN
    SELECT count(*) INTO v FROM organization_memberships m JOIN roles r ON r.id = m.role_id
     WHERE m.organization_id = p_org AND m.status = 'active'
       AND r.key IN ('accountant', 'assistant_accountant', 'staff');
  ELSIF p_key = 'limit.devices' THEN
    SELECT count(*) INTO v FROM devices WHERE organization_id = p_org AND status = 'active';
  ELSIF p_key = 'limit.workspace_templates' THEN
    SELECT count(*) INTO v FROM workspace_templates WHERE organization_id = p_org AND archived_at IS NULL;
  ELSIF p_key = 'quota.storage_bytes' THEN
    -- billable: every stored ORIGINAL object that has not been purged (derived page images are not billed)
    SELECT COALESCE(sum(size_bytes), 0) INTO v FROM storage_objects
     WHERE organization_id = p_org AND state IN ('stored', 'verified') AND purpose = 'original';
  ELSIF p_key = 'quota.documents_per_month' THEN
    SELECT count(*) INTO v FROM documents WHERE organization_id = p_org AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.ocr_pages_per_month' THEN
    SELECT COALESCE(sum(page_count), 0) INTO v FROM ocr_results WHERE organization_id = p_org AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.ai_extractions_per_month' THEN
    SELECT count(*) INTO v FROM extractions WHERE organization_id = p_org AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.bank_statements_per_month' THEN
    SELECT count(*) INTO v FROM bank_statements WHERE organization_id = p_org
       AND extraction_id IN (SELECT id FROM extractions WHERE organization_id = p_org AND created_at >= date_trunc('month', now()));
  ELSIF p_key = 'limit.processing_concurrency' THEN
    SELECT count(*) INTO v FROM processing_jobs WHERE organization_id = p_org
       AND status IN ('queued', 'preparing_pages', 'ocr_processing', 'classifying', 'extracting', 'validating', 'suggesting');
  ELSIF p_key = 'quota.accounting_transactions_per_month' THEN
    SELECT count(*) INTO v FROM acct_transactions WHERE organization_id = p_org AND reversal_of_id IS NULL AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.journals_per_month' THEN
    SELECT count(*) INTO v FROM acct_journals WHERE organization_id = p_org AND reversal_of_id IS NULL AND created_at >= date_trunc('month', now());
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;
