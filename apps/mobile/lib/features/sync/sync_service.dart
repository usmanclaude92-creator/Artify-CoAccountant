import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;

import '../../core/network/api_client.dart';
import '../../core/network/api_error.dart';
import '../upload/upload_models.dart';
import '../upload/upload_queue.dart';

/// What the phone tells the user about synchronization. Only [synchronized] means the server confirmed it.
enum SyncState { pending, uploading, failed, conflictDetected, synchronized }

/// The single place that decides the state. Pure, so the rules can be tested without a server: "synchronized" needs nothing
/// waiting or failed, a completed read of the server's change feed, and the server itself reporting zero open conflicts.
(SyncState, String) foldSyncState({
  required int waiting,
  required int uploading,
  required int failed,
  required int? openConflicts,
  required bool pulled,
  required bool caughtUp,
  String? lastError,
}) {
  if ((openConflicts ?? 0) > 0) {
    return (SyncState.conflictDetected, 'Some changes conflict with newer versions on the server. Review them.');
  }
  if (failed > 0) {
    return (SyncState.failed, 'Some documents could not be uploaded.');
  }
  if (uploading > 0) {
    return (SyncState.uploading, 'Uploading…');
  }
  if (waiting > 0) {
    return (SyncState.pending, 'Waiting to upload.');
  }
  if (lastError != null) {
    return (SyncState.failed, 'The last check with the server failed.');
  }
  if (!pulled || !caughtUp) {
    return (SyncState.pending, 'Not yet checked against the server.');
  }
  if (openConflicts == null) {
    return (SyncState.pending, 'The server could not confirm the state.');
  }
  return (SyncState.synchronized, 'Everything is confirmed by the server.');
}

class SyncConflict {
  const SyncConflict({
    required this.id,
    required this.itemKind,
    required this.itemId,
    required this.type,
    required this.baseRevision,
    required this.serverRevision,
    required this.state,
    this.resolution,
    this.nextBaseRevision,
  });
  final String id;
  final String itemKind;
  final String itemId;
  final String type; // stale | locked
  final int? baseRevision;
  final int? serverRevision;
  final String state; // open | resolved
  final String? resolution;
  final int? nextBaseRevision;
  bool get isLocked => type == 'locked';

  factory SyncConflict.fromJson(Map<String, dynamic> j) => SyncConflict(
    id: j['id'] as String,
    itemKind: j['item_kind'] as String,
    itemId: j['item_id'] as String,
    type: j['conflict_type'] as String,
    baseRevision: (j['base_revision'] as num?)?.toInt(),
    serverRevision: (j['server_revision'] as num?)?.toInt(),
    state: j['state'] as String,
    resolution: j['resolution'] as String?,
    nextBaseRevision: (j['next_base_revision'] as num?)?.toInt(),
  );
}

class PullSummary {
  const PullSummary({
    required this.pages,
    required this.items,
    required this.caughtUp,
    required this.restarted,
  });
  final int pages;
  final int items;
  final bool caughtUp;
  final bool restarted;
}

class SyncView {
  const SyncView({
    required this.state,
    required this.label,
    required this.waiting,
    required this.uploading,
    required this.failed,
    required this.openConflicts,
    required this.lastPullAt,
    required this.lastError,
    required this.knownItems,
  });
  final SyncState state;
  final String label;
  final int waiting;
  final int uploading;
  final int failed;
  final int? openConflicts;
  final DateTime? lastPullAt;
  final String? lastError;
  final Map<String, int> knownItems;
}

/// Cloud synchronization for the phone (Phase 12). The phone keeps a checkpoint of the server's change feed per company and
/// asks the server for the truth about conflicts; it never decides that something is synchronized by itself. The checkpoint
/// and the items it covers are written together (temporary file, then rename), so an interruption re-reads a page and never
/// skips one.
class SyncService extends ChangeNotifier {
  SyncService({
    required this.api,
    required this.queue,
    required this.dir,
    DateTime Function()? now,
  }) : _now = now ?? DateTime.now;
  final ApiClient api;
  final UploadQueue queue;
  final Directory dir;
  final DateTime Function() _now;

  static const _page = 200;
  static const _maxPages = 50;
  static const _overlapSeconds = 5;

  SyncView? view;
  List<SyncConflict> conflicts = const [];
  bool busy = false;
  String? message;

  File _file(String company) => File(p.join(dir.path, 'sync-$company.json'));

  Future<Map<String, dynamic>> _read(String company) async {
    final f = _file(company);
    if (!await f.exists()) {
      return {'items': <String, dynamic>{}};
    }
    try {
      return jsonDecode(await f.readAsString()) as Map<String, dynamic>;
    } on Object {
      return {'items': <String, dynamic>{}}; // a damaged checkpoint only means reading the feed again from the start
    }
  }

  Future<void> _write(String company, Map<String, dynamic> state) async {
    await dir.create(recursive: true);
    final f = _file(company);
    final tmp = File('${f.path}.tmp');
    await tmp.writeAsString(jsonEncode(state), flush: true);
    await tmp.rename(f.path);
  }

  /// Reads the change feed to its end (or [_maxPages] pages). Safe to repeat.
  Future<PullSummary> pull(String org, String company) async {
    var state = await _read(company);
    var pages = 0, total = 0;
    var restarted = false;
    for (var i = 0; i < _maxPages; i++) {
      final cursor = state['cursor'] as String?;
      Map<String, dynamic> page;
      try {
        page = await api.getJson(
          '/companies/$company/sync/changes',
          org: org,
          query: {
            'limit': '$_page',
            'cursor': ?cursor,
            if (cursor != null) 'overlap_seconds': '$_overlapSeconds',
          },
        );
      } on ApiError catch (e) {
        if (e.code == 'invalid_cursor' && !restarted) {
          state = {'items': <String, dynamic>{}};
          await _write(company, state);
          restarted = true;
          continue;
        }
        state['last_error'] = e.code;
        state['caught_up'] = false;
        await _write(company, state);
        rethrow;
      }
      final items = (state['items'] as Map).cast<String, dynamic>();
      for (final raw in (page['items'] as List? ?? const [])) {
        final it = (raw as Map).cast<String, dynamic>();
        final kind =
            (items[it['kind']] as Map?)?.cast<String, dynamic>() ??
            <String, dynamic>{};
        final rev = (it['revision'] as num).toInt();
        if (rev >= ((kind[it['id']] as num?)?.toInt() ?? 0)) {
          kind[it['id'] as String] = rev; // newest revision wins
        }
        items[it['kind'] as String] = kind;
        total++;
      }
      final more = page['has_more'] == true;
      state = {
        'cursor': page['next_cursor'] ?? cursor,
        'items': items,
        'caught_up': !more,
        'last_pull_at': _now().toUtc().toIso8601String(),
      };
      await _write(company, state);
      pages++;
      if (!more) break;
    }
    return PullSummary(
      pages: pages,
      items: total,
      caughtUp: state['caught_up'] == true,
      restarted: restarted,
    );
  }

  /// Local queue + checkpoint + the server's own conflict count, folded into one honest state.
  Future<SyncView> status(String org, String company) async {
    final state = await _read(company);
    final mine = queue.forCompany(company);
    int n(bool Function(QueueItem) f) => mine.where(f).length;
    final waiting = n(
      (i) => i.state == UploadState.waiting || i.state == UploadState.verifying,
    );
    final uploading = n((i) => i.state == UploadState.uploading);
    final failed = n(
      (i) =>
          i.state == UploadState.failed || i.state == UploadState.quarantined,
    );
    int? open;
    try {
      open =
          ((await api.getJson(
                    '/companies/$company/sync/status',
                    org: org,
                  ))['open_conflicts']
                  as num?)
              ?.toInt();
    } on ApiError {
      open = null; // not asking the server successfully is never "synchronized"
    }
    final pulledAt = state['last_pull_at'] as String?;
    final fold = foldSyncState(
      waiting: waiting,
      uploading: uploading,
      failed: failed,
      openConflicts: open,
      pulled: pulledAt != null,
      caughtUp: state['caught_up'] == true,
      lastError: state['last_error'] as String?,
    );
    final known = <String, int>{
      for (final e in ((state['items'] as Map?) ?? const {}).entries)
        e.key as String: (e.value as Map).length,
    };
    return SyncView(
      state: fold.$1,
      label: fold.$2,
      waiting: waiting,
      uploading: uploading,
      failed: failed,
      openConflicts: open,
      lastPullAt: pulledAt == null ? null : DateTime.parse(pulledAt),
      lastError: state['last_error'] as String?,
      knownItems: known,
    );
  }

  Future<List<SyncConflict>> openConflicts(String org, String company) async {
    final j = await api.getJson(
      '/companies/$company/sync/conflicts',
      org: org,
      query: {'state': 'open'},
    );
    return [
      for (final e in (j['items'] as List? ?? const []))
        SyncConflict.fromJson((e as Map).cast<String, dynamic>()),
    ];
  }

  /// Records the person's decision. The server may refuse (locked record, missing explanation, not allowed): its words are shown.
  /// It does not write the record; for apply/merge the result carries the revision to send the change on top of.
  Future<SyncConflict> resolve(
    String org,
    String company,
    String conflictId,
    String resolution, {
    String? note,
  }) async {
    final j = await api.postJson(
      '/companies/$company/sync/conflicts/$conflictId/resolve',
      org: org,
      body: {'resolution': resolution, 'note': ?note},
    );
    return SyncConflict.fromJson(j);
  }

  /// Screen entry point: refresh what is shown. Errors become a message, never an exception in the UI.
  Future<void> refresh(String org, String company) async {
    try {
      view = await status(org, company);
      conflicts = await openConflicts(org, company);
    } on ApiError catch (e) {
      message = e.isNetwork
          ? 'The server cannot be reached. Showing the last known state.'
          : e.message;
    }
    notifyListeners();
  }

  Future<void> checkNow(String org, String company) async {
    busy = true;
    message = null;
    notifyListeners();
    try {
      final r = await pull(org, company);
      message = r.caughtUp
          ? 'Checked with the server: ${r.items} change(s) read.'
          : 'More changes remain; check again to continue.';
    } on ApiError catch (e) {
      message = e.isNetwork ? 'The server cannot be reached.' : e.message;
    }
    busy = false;
    await refresh(org, company);
  }

  Future<void> decide(
    String org,
    String company,
    SyncConflict c,
    String resolution, {
    String? note,
  }) async {
    message = null;
    try {
      final r = await resolve(org, company, c.id, resolution, note: note);
      message = r.nextBaseRevision != null
          ? 'Decision recorded. Send your change on top of server revision ${r.nextBaseRevision}.'
          : 'Decision recorded. The server copy is kept.';
    } on ApiError catch (e) {
      message = e.message;
    }
    await refresh(org, company);
  }

  Future<void> wipe() async {
    if (await dir.exists()) await dir.delete(recursive: true);
    view = null;
    conflicts = const [];
    notifyListeners();
  }
}
