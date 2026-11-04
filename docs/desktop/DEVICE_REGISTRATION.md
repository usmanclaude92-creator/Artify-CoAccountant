# Device Registration

## Flow

After the organization is chosen, `device_ensure` runs:

1. No local record ⇒ `POST /devices` `{platform:"windows", name:<computer name>, app_version}` ⇒ the server enforces `limit.devices`, issues the **device id**, and binds the registering session to it. The id, name, timestamps and status are stored locally.
2. Local record ⇒ `POST /devices/{id}/session`: binds the *current* session to this device and records activity (heartbeat; also on every later start/sign-in).
3. The server answers 404 (revoked, removed or not this user's) ⇒ local status `revoked`, notification, session ended, cache wiped, message "This device has been revoked. Contact your administrator."

Settings → Devices lists the user's devices (`GET /devices`: name, platform, version, last active, status) and can revoke one after confirmation (`DELETE /devices/{id}`).

## Server-side enforcement (added in Phase 5)

- Sessions have a `device_id`. Registering binds the current session; `bind` re-attaches a later session. Revoking a device marks it revoked **and revokes every session bound to it**, so its access token stops working at once and its refresh token cannot be rotated.
- A revoked device id can never be attached to a new session (404). The client-generated name or id is not a security control: the id is issued by the server, validated on every bind, and limited by the subscription (`limit.devices`, 402 `entitlement_exceeded` surfaces as a plain message; no local record is created without a server registration).

## Tested

Backend (`test_p5_devices.py`): registration binds the session; revocation ends it and blocks refresh; rebind works for own active device only; foreign and revoked devices are 404; limits apply. Core live test against a real API: register, heartbeat, revoke from a second computer ⇒ first installation's next call is `session_expired`, vault emptied, cache wiped, restore impossible, rebind refused.

## Limits and not built

Revocation is effective at the *next request* of the revoked installation (no push); there is no remote wipe command beyond the automatic cache wipe on contact; the device list for **all** users in the Control Center ("Devices" module) is not built; only `windows` devices are registered by this app; `key_fingerprint` is not sent (no device key pair); minimum-version enforcement does not exist server-side.
