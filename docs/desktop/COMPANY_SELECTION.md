# Organization and Company Selection

## Organizations

`GET /orgs` lists the organizations the user belongs to. One organization ⇒ the app goes straight in; several ⇒ an organization picker (also switchable from the top bar and Settings → Account); none ⇒ an explanatory message. The organization id is sent to the API only as the `X-Organization-Id` **claim**, which the backend validates against the user's memberships on every request.

## Companies

`GET /orgs/{org}/companies` returns exactly the companies this user may reach (organization-wide roles see all; assigned roles see their assignments; client-side roles see their own). The list shows name, code, country, currency and status (Active / Inactive / Archived) with search (name, code, country), filters (all, active only, favorites, recently opened) and a refresh. A list loaded offline is marked as the saved copy.

## Opening a company is always verified

`company_select` calls `GET /companies/{id}` with the organization claim. A company the user may not reach answers 404 (also for a typed or guessed id): the app shows "You no longer have access to this company, or it does not exist", removes it and its cached workspace from the local cache, and changes nothing else (the "last company" setting is untouched). A selection answered with a different company's data is refused as malformed. Opening is not possible offline, because the server must confirm access.

## Local conveniences (not authorization)

Favorites and "last opened" are stored locally per user and are **not synchronized between devices**. A "default company" (Settings → General) is opened automatically at startup only if it is still in the server's list and active; the server still verifies it.

## Assigned accountants

The workspace header shows the active assigned accountants when the backend returns them (roles with `assignments.read`); other roles simply do not see them.

## Not available

Company logos — the backend has no logo field yet. Cross-device favorites.
