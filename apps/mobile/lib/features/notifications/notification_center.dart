import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/network/api_error.dart';
import '../documents/documents_repository.dart';
import '../upload/upload_models.dart';
import '../upload/upload_queue.dart';

enum NoticeKind { uploadCompleted, uploadFailed, quarantined, documentEvent, serverNotification }

class Notice {
  const Notice({required this.id, required this.kind, required this.title, required this.detail, required this.at, this.documentId, this.readOnServer = false});
  final String id;
  final NoticeKind kind;
  final String title;
  final String detail;
  final DateTime at;
  final String? documentId;

  /// A server notification the person already read (here or on another device).
  final bool readOnServer;
}

/// What the phone can honestly tell the user today, built from real records: the upload queue (this device) and the
/// company's document events (the server). It is NOT a push channel: nothing arrives while the app is closed, because no
/// push provider is configured (see docs/mobile/NOTIFICATIONS.md). Texts never contain amounts, names of parties or
/// document content, so they are safe to show on a lock screen if push is added later.
class NotificationCenter extends ChangeNotifier {
  NotificationCenter({required this.queue, required this.docs, required this.prefs});
  final UploadQueue queue;
  final DocumentsRepository docs;
  final SharedPreferences prefs;

  static const _kSeen = 'notices.seen';
  static const _kUpload = 'notices.pref.uploads';
  static const _kServer = 'notices.pref.server';

  List<Notice> serverNotices = const [];
  String? error;

  bool get showUploadNotices => prefs.getBool(_kUpload) ?? true;
  bool get showServerNotices => prefs.getBool(_kServer) ?? true;
  Future<void> setShowUploadNotices(bool v) async {
    await prefs.setBool(_kUpload, v);
    notifyListeners();
  }

  Future<void> setShowServerNotices(bool v) async {
    await prefs.setBool(_kServer, v);
    notifyListeners();
  }

  Set<String> get _seen => (prefs.getStringList(_kSeen) ?? const []).toSet();

  List<Notice> notices(String companyId) {
    final out = <Notice>[];
    if (showUploadNotices) {
      for (final i in queue.forCompany(companyId)) {
        switch (i.state) {
          case UploadState.completed:
            out.add(Notice(id: 'q-${i.id}-done', kind: NoticeKind.uploadCompleted, title: 'Upload verified', detail: '${i.displayName} is stored in the cloud.', at: i.updatedAt, documentId: i.documentId));
          case UploadState.failed:
            out.add(Notice(id: 'q-${i.id}-fail', kind: NoticeKind.uploadFailed, title: 'Upload failed', detail: '${i.displayName}: ${i.errorMessage ?? 'The server did not accept it.'}', at: i.updatedAt));
          case UploadState.quarantined:
            out.add(Notice(id: 'q-${i.id}-quar', kind: NoticeKind.quarantined, title: 'Document quarantined', detail: '${i.displayName} was held by the server for safety.', at: i.updatedAt));
          default:
        }
      }
    }
    if (showServerNotices) out.addAll(serverNotices);
    out.sort((a, b) => b.at.compareTo(a.at));
    return out;
  }

  int unread(String companyId) => notices(companyId).where(isUnread).length;
  bool isUnread(Notice n) => !n.readOnServer && !_seen.contains(n.id);

  Future<void> markAllRead(String companyId) async {
    await prefs.setStringList(_kSeen, {..._seen, ...notices(companyId).map((n) => n.id)}.toList());
    notifyListeners();
    try {
      await docs.api.postJson('/notifications/read-all'); // best effort: the local "seen" list already covers this device
    } on ApiError {
      // offline: the server copy is marked the next time
    }
  }

  /// Fetches the company's recent document events. Failures keep the previous list and say so.
  Future<void> refresh(String companyId) async {
    try {
      final events = await docs.events(companyId);
      final list = <Notice>[];
      for (final e in events) {
        final d = _describe(e);
        if (d == null) continue;
        list.add(Notice(id: 'e-${e['id']}', kind: NoticeKind.documentEvent, title: d.$1, detail: d.$2, at: DateTime.parse(e['created_at'] as String), documentId: e['document_id'] as String?));
      }
      list.addAll(await _personal());
      serverNotices = list;
      error = null;
    } on ApiError catch (e) {
      error = e.isNetwork ? 'Showing what is on this device; the server cannot be reached.' : e.message;
    }
    notifyListeners();
  }

  /// The person's own notifications from the server (task assignments, closing results). The server writes generic text only;
  /// a failure here never hides the rest.
  Future<List<Notice>> _personal() async {
    try {
      final j = await docs.api.getJson('/notifications', query: {'limit': '30'});
      return [
        for (final e in (j['items'] as List? ?? const []))
          Notice(
            id: 'n-${(e as Map)['id']}',
            kind: NoticeKind.serverNotification,
            title: e['title'] as String,
            detail: (e['body'] as String?) ?? '',
            at: DateTime.parse(e['created_at'] as String),
            readOnServer: e['read_at'] != null,
          ),
      ];
    } on ApiError {
      return const [];
    }
  }

  /// Only generic wording: the event type, never the document's name or content.
  static (String, String)? _describe(Map<String, dynamic> e) {
    final t = (e['event_type'] as String?) ?? '';
    if (t.startsWith('processing.')) {
      if (t.contains('failed')) return ('Processing failed', 'A document could not be processed and needs attention.');
      if (t.contains('awaiting_review') || t.contains('review')) return ('Document needs review', 'A document is waiting for review.');
      if (t.contains('completed') || t.contains('approved')) return ('Processing completed', 'A document has been processed.');
    }
    if (t == 'document.rejected') return ('Document rejected', 'A document was rejected. Open it for details.');
    if (t == 'document.quarantined') return ('Document quarantined', 'A document was held by the server for safety.');
    if (t.startsWith('workspace.')) return ('Workspace updated', 'The company workspace was changed. Pull to refresh your folders.');
    return null;
  }
}
