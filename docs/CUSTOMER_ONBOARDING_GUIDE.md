# Customer Onboarding Guide

For an accounting firm's owner or practice manager starting with Artify Co-Accountant. It describes the product **as built**; where something is not available yet it says so. Screens are in the Firm Control Center (web), the Windows desktop app and the phone app. The desktop and phone apps have not been installed from a release build yet, so installation steps below apply once a build is issued ([WINDOWS_RELEASE_GUIDE](WINDOWS_RELEASE_GUIDE.md), [ANDROID_RELEASE_GUIDE](ANDROID_RELEASE_GUIDE.md), [IOS_RELEASE_GUIDE](IOS_RELEASE_GUIDE.md)).

## Before you start

You need: a work e-mail address, an authenticator app (owners, administrators and practice managers must use two-factor sign-in), and an idea of how you want to organize each client's documents. Artify does not impose a folder structure — you build it, or start from a template that is **copied** into your company and can then be changed freely.

## Day 1 — your firm

1. **Sign up** and confirm your e-mail. You become the owner of a new organization (your firm). Your plan and limits (companies, users, devices, storage) are shown under Subscription; limits are enforced by the server. *Self-service payment is not available: subscriptions are recorded by Artify staff.*
2. **Turn on two-factor sign-in** when prompted.
3. **Invite your team** (Team → Invite). Choose a role: practice manager, accountant, assistant accountant, document operator, read-only, or a client-side role. Invitations expire after 7 days.
4. **Create the first company** (Companies → New company): name, country, currency, fiscal year. Each company is separate: its own workspace, accounts, tax setup, documents and closings.

## Day 1 — the company's workspace

5. Open **Workspace**. Start from a template or from nothing; create sections and folders, document categories (each maps to a kind of document the system understands: invoice, receipt, bank statement…), custom fields and naming rules. Renaming never breaks anything; archive instead of delete.
6. **Assign accountants** to the company (Accountants). People see only companies they are assigned to unless their role is organization-wide. In a restricted workspace, folder permissions can narrow access further.
7. Set up **accounting** for the company: chart of accounts, tax codes (the Oman pack is a starting point that **must be reviewed by a qualified tax professional**), accounting periods.

## Daily work

8. **Get documents in**: drag them into the Windows app, or scan with the phone app, or upload in the browser. Each is virus-checked, de-duplicated and filed in the folder you choose. "Saved" appears only after the server confirms.
9. **Process**: text is read from the document and suggestions are made (type, supplier, dates, amounts). Suggestions are never final — an accountant reviews and approves the data.
10. **Accounting**: approved data becomes a draft transaction with a proposed journal. A different person than the preparer approves it (maker–checker). Approved records cannot be edited; corrections are reversals or new versions.
11. **Excel**: upload your own workbook as a template, map its columns once, and export approved transactions into it. Your formatting and formulas are preserved; every export is kept with its history.
12. **Daily closing**: at day end, Daily Bookkeeping validates the day (missing documents, unbalanced entries, unapproved items, tax checks, open tasks), a preparer submits, and a second person with approval rights closes it. A closed day is locked; reopening needs a reason and a permitted person.

## What is not there yet

Delivery of notifications (they are queued, not sent); push on the phone; automatic conflict-resolution screens; per-tenant encryption keys; online payment; a status page. Ask Artify before relying on any of these.

## Getting help

Use the support request in the Control Center. Artify staff cannot see your business data unless you grant time-limited, audited support access.

## Firm checklist for the first week

Two-factor on for every owner/administrator • roles reviewed • one pilot company fully configured • five real (non-sensitive, or consented) documents taken through upload → approval → export → daily close • the closing snapshot reviewed by the accountant who would sign it • a decision on who may reopen closed days.
