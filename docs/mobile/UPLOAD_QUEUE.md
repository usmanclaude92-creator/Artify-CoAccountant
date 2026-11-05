# Upload queue

* **Stored where:** the app's private support directory: `queue/queue.json` (written atomically) and `queue/files/<id>.<ext>` (a copy of the document). Not shared storage; other apps cannot read it.
* **States shown:** Draft (saved on this phone) · Waiting to upload / Saved on this device — waiting for a connection · Uploading · Verifying in cloud storage · **Verified in cloud storage** (only after `POST …/complete` returns the stored document) · Failed (with the server's reason) · Cancelled · Quarantined by the server.
* **Protocol:** register (idempotency key `mob-<uuid>` stored with the item) → send bytes → complete. A repeat of any step returns the first result, so an interrupted or restarted upload cannot create a second document.
* **Retries:** network errors, timeouts, 5xx and 429 keep the item *waiting* with exponential back-off (10 s doubling to 15 min) and it also retries on reconnect and when the app returns to the foreground. A server refusal (type, destination, validation, limit) is **failed** and is not retried until the person fixes the cause and taps Retry. A session that has ended pauses the queue (documents are kept) until the next sign-in.
* **Duplicates:** the same file for the same destination cannot be queued twice; the server's duplicate policy is `block` unless the person chooses "upload as a copy".
* **Cleanup:** the local copy is deleted once the server confirmed the document, or on cancel; finished records are pruned after 14 days; sign-out and "clear data" remove the queue.
* **Not done:** byte-range resume (the backend takes whole files), background upload while the app is closed (needs platform background services), encryption of the queue files beyond the OS sandbox and file-based encryption.
* **Not Phase 12:** this is an upload queue, not the synchronisation engine. The app never calls a document "synchronised".
