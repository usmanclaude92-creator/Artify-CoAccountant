import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:uuid/uuid.dart';

import 'upload_models.dart';
import 'upload_validator.dart';

class EnqueueRejected implements Exception {
  const EnqueueRejected(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The phone's upload queue on disk: a JSON journal (written atomically) plus a private copy of each document. It survives
/// closing the app. Documents live in the app's own storage area, which other apps cannot read; they are removed once the
/// server has verified them, or when the person cancels.
class UploadQueue extends ChangeNotifier {
  UploadQueue(this.dir, {Uuid? uuid}) : _uuid = uuid ?? const Uuid();
  final Directory dir;
  final Uuid _uuid;
  final List<QueueItem> _items = [];
  bool _loaded = false;

  List<QueueItem> get items => List.unmodifiable(_items);

  File get _journal => File(p.join(dir.path, 'queue.json'));
  Directory get _files => Directory(p.join(dir.path, 'files'));

  Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    await _files.create(recursive: true);
    if (await _journal.exists()) {
      try {
        final list = jsonDecode(await _journal.readAsString()) as List;
        _items.addAll(list.map((e) => QueueItem.fromJson(e as Map<String, dynamic>)));
      } on Object {
        // A damaged journal must not hide the files: keep it for support and start a fresh one.
        await _journal.copy(p.join(dir.path, 'queue.damaged.json'));
      }
    }
    // An upload that was running when the app was closed is simply waiting again; the idempotency key makes the retry safe.
    for (final i in _items) {
      if (i.state == UploadState.uploading || i.state == UploadState.verifying) {
        i.state = UploadState.waiting;
        i.progress = 0;
      }
    }
    await _save();
    notifyListeners();
  }

  /// Copies [sourcePath] into the queue and registers it. Nothing is sent here. Throws [EnqueueRejected] for a file that
  /// can never be uploaded (empty, unsupported type) or that is already queued for this destination.
  Future<QueueItem> enqueue({
    required String sourcePath,
    required String organizationId,
    required String companyId,
    required String nodeId,
    String? categoryId,
    Map<String, Object?> metadata = const {},
    String? displayName,
    UploadSource source = UploadSource.file,
    int? pages,
    int? maxBytes,
    List<String>? allowedTypes,
    bool asDraft = false,
  }) async {
    await load();
    final src = File(sourcePath);
    if (!await src.exists()) throw const EnqueueRejected('The file could not be found.');
    final name = p.basename(sourcePath);
    final size = await src.length();
    final check = UploadValidator.check(name, size, maxBytes: maxBytes, allowedForCategory: allowedTypes);
    if (!check.isOk) throw EnqueueRejected(check.problem!);
    final digest = (await sha256.bind(src.openRead()).first).toString();
    final dup = _items.where((i) => !i.isFinished && i.state != UploadState.failed && i.companyId == companyId && i.nodeId == nodeId && i.sha256 == digest).firstOrNull;
    if (dup != null) throw const EnqueueRejected('This exact file is already waiting to be uploaded to this destination.');
    final id = _uuid.v4();
    final stored = p.join(_files.path, '$id${p.extension(name)}');
    await _files.create(recursive: true);
    await src.copy(stored);
    final item = QueueItem(
      id: id,
      organizationId: organizationId,
      companyId: companyId,
      fileName: name,
      displayName: (displayName ?? '').trim().isEmpty ? p.basenameWithoutExtension(name) : displayName!.trim(),
      mime: UploadValidator.mimeOf(name),
      size: size,
      sha256: digest,
      localPath: stored,
      nodeId: nodeId,
      categoryId: categoryId,
      metadata: metadata,
      idempotencyKey: 'mob-${_uuid.v4()}',
      source: source,
      createdAt: DateTime.now().toUtc(),
      pages: pages,
      state: asDraft ? UploadState.draft : UploadState.waiting,
    );
    _items.add(item);
    await _save();
    notifyListeners();
    return item;
  }

  QueueItem? byId(String id) => _items.where((i) => i.id == id).firstOrNull;

  Future<void> update(QueueItem item) async {
    item.updatedAt = DateTime.now().toUtc();
    await _save();
    notifyListeners();
  }

  /// A draft becomes eligible for upload.
  Future<void> submitDraft(String id) async {
    final i = byId(id);
    if (i == null || i.state != UploadState.draft) return;
    i.state = UploadState.waiting;
    await update(i);
  }

  /// The person asks to try again (or to upload as a copy after a duplicate warning).
  Future<void> retry(String id, {bool asCopy = false}) async {
    final i = byId(id);
    if (i == null || !(i.state == UploadState.failed || i.state == UploadState.quarantined || i.state == UploadState.waiting)) return;
    if (i.state == UploadState.quarantined) return; // a quarantined file is not retried automatically or by hand
    i.state = UploadState.waiting;
    i.errorCode = null;
    i.errorMessage = null;
    i.nextAttemptAt = null;
    if (asCopy) i.allowCopy = true;
    await update(i);
  }

  /// Stops a pending upload and removes the local copy. (A document already stored in the cloud is not touched.)
  Future<void> cancel(String id) async {
    final i = byId(id);
    if (i == null || i.state == UploadState.completed) return;
    i.state = UploadState.cancelled;
    await _deleteFile(i);
    await update(i);
  }

  /// Called after the server confirmed the document: the local copy is no longer needed.
  Future<void> markCompleted(QueueItem i, String documentId) async {
    i.state = UploadState.completed;
    i.documentId = documentId;
    i.progress = 1;
    i.errorCode = null;
    i.errorMessage = null;
    await _deleteFile(i);
    await update(i);
  }

  Future<void> remove(String id) async {
    final i = byId(id);
    if (i == null) return;
    await _deleteFile(i);
    _items.remove(i);
    await _save();
    notifyListeners();
  }

  /// Drops records of finished items older than [olderThan] (cancelled and completed ones are only history).
  Future<void> pruneFinished({Duration olderThan = const Duration(days: 14)}) async {
    final cutoff = DateTime.now().toUtc().subtract(olderThan);
    _items.removeWhere((i) => i.isFinished && i.updatedAt.isBefore(cutoff));
    await _save();
    notifyListeners();
  }

  /// Everything pending for one company (used by the dashboard).
  Iterable<QueueItem> forCompany(String companyId) => _items.where((i) => i.companyId == companyId);

  Future<int> bytesOnDevice() async {
    var n = 0;
    for (final i in _items) {
      final f = File(i.localPath);
      if (await f.exists()) n += await f.length();
    }
    return n;
  }

  /// Sign-out or "clear data": nothing of the queue remains.
  Future<void> wipe() async {
    _items.clear();
    if (await dir.exists()) await dir.delete(recursive: true);
    _loaded = false;
    notifyListeners();
  }

  Future<void> _deleteFile(QueueItem i) async {
    final f = File(i.localPath);
    if (await f.exists()) await f.delete();
  }

  Future<void> _save() async {
    await dir.create(recursive: true);
    final tmp = File('${_journal.path}.tmp');
    await tmp.writeAsString(jsonEncode([for (final i in _items) i.toJson()]), flush: true);
    await tmp.rename(_journal.path); // atomic replace: a crash never leaves half a journal
  }
}
