import 'dart:io';
import 'dart:typed_data';

import '../../core/network/api_client.dart';
import '../../core/network/api_error.dart';
import 'upload_models.dart';
import 'upload_queue.dart';

/// Runs ONE queued upload through the server's three-step protocol: register (`POST …/documents/uploads`), send the bytes
/// (`PUT …/content`), complete (`POST …/complete`). A document counts as uploaded only when `complete` answers with the
/// stored document; until then it stays "waiting", "uploading" or "verifying". Repeating any step with the same idempotency
/// key returns the first result, so an interrupted upload can be retried without creating a duplicate.
class UploadService {
  UploadService(this.api, this.queue, {DateTime Function()? now}) : _now = now ?? DateTime.now;
  final ApiClient api;
  final UploadQueue queue;
  final DateTime Function() _now;

  /// Base delay for the retry back-off; doubles with every failed attempt up to [maxBackoff].
  static const baseBackoff = Duration(seconds: 10);
  static const maxBackoff = Duration(minutes: 15);

  Future<void> run(QueueItem item) async {
    final base = '/companies/${item.companyId}/documents/uploads';
    try {
      item.state = UploadState.uploading;
      item.attempts += 1;
      item.progress = 0.05;
      item.nextAttemptAt = null;
      await queue.update(item);

      // 1. register (idempotent: the same key returns the same operation)
      final op = await api.postJson(base, org: item.organizationId, body: {
        'filename': item.fileName,
        'size': item.size,
        'sha256': item.sha256,
        'mime': item.mime,
        'node_id': item.nodeId,
        if (item.categoryId != null) 'category_id': item.categoryId,
        'display_name': item.displayName,
        'metadata': item.metadata,
        'idempotency_key': item.idempotencyKey,
        'duplicate_policy': item.allowCopy ? 'allow_copy' : 'block',
      });
      item.serverOperationId = op['id'] as String?;
      var status = op['status'] as String?;
      item.progress = 0.15;
      await queue.update(item);

      String? documentId = op['result_document_id'] as String?;
      if (status != 'completed') {
        // 2. the bytes (skipped when the server already has them from an earlier, interrupted attempt)
        if (status == 'awaiting_upload' || status == 'uploading' || status == null) {
          final file = File(item.localPath);
          if (!await file.exists()) {
            throw const ApiError(status: 410, code: 'local_file_missing', message: 'The saved copy of this document is no longer on the device.');
          }
          final Uint8List bytes = await file.readAsBytes();
          final sent = await api.putBytes('$base/${item.serverOperationId}/content', bytes, org: item.organizationId);
          status = (sent['operation'] is Map ? (sent['operation'] as Map)['status'] : sent['status']) as String? ?? 'uploaded';
          item.progress = 0.8;
        }
        item.state = UploadState.verifying;
        await queue.update(item);

        // 3. complete: the server verifies the stored object and creates the document
        final done = await api.postJson('$base/${item.serverOperationId}/complete', org: item.organizationId);
        final operation = (done['operation'] as Map?)?.cast<String, dynamic>() ?? done;
        final doc = (done['document'] as Map?)?.cast<String, dynamic>();
        if (operation['status'] != 'completed' || doc == null || doc['id'] == null) {
          // Never report success the server did not confirm.
          throw ApiError(status: 502, code: 'not_confirmed', message: 'The server has not confirmed this upload yet. It will be checked again.');
        }
        documentId = doc['id'] as String;
      }
      if (documentId == null) {
        throw const ApiError(status: 502, code: 'not_confirmed', message: 'The server has not confirmed this upload yet. It will be checked again.');
      }
      await queue.markCompleted(item, documentId);
    } on ApiError catch (e) {
      await _record(item, e);
    }
  }

  Future<void> _record(QueueItem item, ApiError e) async {
    item.errorCode = e.code;
    item.progress = 0;
    final opStatus = (e.extra['operation'] is Map) ? (e.extra['operation'] as Map)['status'] : null;
    if (e.isSessionExpired) {
      // Not the document's fault: keep it waiting; it continues after the person signs in again.
      item.state = UploadState.waiting;
      item.errorMessage = 'Sign in again to continue uploading.';
      item.retryable = true;
    } else if (opStatus == 'quarantined' || e.code == 'quarantined') {
      item.state = UploadState.quarantined;
      item.errorMessage = e.message;
      item.retryable = false;
    } else if (e.isRetryable || e.code == 'not_confirmed') {
      // offline, timeout, server trouble: stays queued and tries again later
      item.state = UploadState.waiting;
      item.errorMessage = e.isNetwork ? 'No connection. This will upload when you are back online.' : e.message;
      item.retryable = true;
      item.nextAttemptAt = _now().toUtc().add(_backoff(item.attempts));
    } else {
      item.state = UploadState.failed;
      item.retryable = false;
      item.errorMessage = e.isEntitlement ? '${e.message} Ask your administrator about the plan limits.' : e.message;
    }
    await queue.update(item);
  }

  Duration _backoff(int attempts) {
    final ms = baseBackoff.inMilliseconds * (1 << (attempts - 1).clamp(0, 10));
    return Duration(milliseconds: ms > maxBackoff.inMilliseconds ? maxBackoff.inMilliseconds : ms);
  }
}
