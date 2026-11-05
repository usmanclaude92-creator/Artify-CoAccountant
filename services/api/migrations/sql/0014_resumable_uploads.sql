-- Phase 12 (continued): resumable uploads. The server records how many contiguous bytes of an upload session it holds,
-- so a client that lost its connection asks "where am I?" and continues from there instead of starting again.
ALTER TABLE document_upload_operations
  ADD COLUMN received_bytes bigint NOT NULL DEFAULT 0 CHECK (received_bytes >= 0),
  ADD CONSTRAINT upload_received_within_declared CHECK (received_bytes <= declared_size);
