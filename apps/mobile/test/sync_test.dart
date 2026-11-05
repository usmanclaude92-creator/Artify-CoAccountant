import 'dart:io';

import 'package:artify_mobile/core/config/app_config.dart';
import 'package:artify_mobile/core/network/api_client.dart';
import 'package:artify_mobile/core/network/api_error.dart';
import 'package:artify_mobile/core/storage/secure_store.dart';
import 'package:artify_mobile/features/documents/documents_repository.dart';
import 'package:artify_mobile/features/notifications/notification_center.dart';
import 'package:artify_mobile/features/sync/sync_service.dart';
import 'package:artify_mobile/features/upload/upload_models.dart';
import 'package:artify_mobile/features/upload/upload_queue.dart';
import 'package:artify_mobile/features/upload/upload_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'support/fake_backend.dart';

const mib = 1024 * 1024;

Map<String, dynamic> item(String kind, String id, int rev) => {
  'kind': kind,
  'id': id,
  'revision': rev,
  'status': 'active',
  'changed_at': '2026-03-02T09:00:00Z',
  'data': {},
};
Map<String, dynamic> conflict(String id, {String type = 'stale'}) => {
  'id': id,
  'item_kind': 'document',
  'item_id': 'd-1',
  'client_id': 'phone',
  'conflict_type': type,
  'base_revision': 3,
  'server_revision': 4,
  'state': 'open',
  'resolution': null,
};

class Rig {
  Rig(this.b, this.api, this.queue, this.sync, this.dir);
  final FakeBackend b;
  final ApiClient api;
  final UploadQueue queue;
  final SyncService sync;
  final Directory dir;
}

Future<Rig> rig({FakeBackend? backend}) async {
  final b = backend ?? FakeBackend();
  final tokens = TokenStore(MemorySecureStore());
  await tokens.save(
    Session(
      accessToken: 'a',
      refreshToken: 'r',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
    ),
  );
  final api = ApiClient(
    config: const AppConfig(apiBaseUrl: 'https://api.test/api/v1'),
    tokens: tokens,
    client: b.client,
  )..organizationId = 'org-1';
  final dir = await Directory.systemTemp.createTemp('sync');
  final queue = UploadQueue(Directory('${dir.path}/queue'));
  return Rig(
    b,
    api,
    queue,
    SyncService(api: api, queue: queue, dir: Directory('${dir.path}/sync')),
    dir,
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('state folding', () {
    test('"synchronized" needs every confirmation', () {
      (SyncState, String) f({
        int w = 0,
        int u = 0,
        int x = 0,
        int? open = 0,
        bool pulled = true,
        bool caught = true,
        String? err,
      }) => foldSyncState(
        waiting: w,
        uploading: u,
        failed: x,
        openConflicts: open,
        pulled: pulled,
        caughtUp: caught,
        lastError: err,
      );
      expect(f().$1, SyncState.synchronized);
      expect(
        f(open: null).$1,
        SyncState.pending,
        reason: 'no answer from the server is not a confirmation',
      );
      expect(f(pulled: false, caught: false).$1, SyncState.pending);
      expect(f(caught: false).$1, SyncState.pending);
      expect(f(w: 1).$1, SyncState.pending);
      expect(f(u: 1).$1, SyncState.uploading);
      expect(f(x: 1).$1, SyncState.failed);
      expect(f(err: 'network_unavailable').$1, SyncState.failed);
      expect(
        f(x: 1, open: 2).$1,
        SyncState.conflictDetected,
        reason: 'conflicts outrank everything',
      );
    });
  });

  group('the change feed', () {
    test('is read in pages, the newest revision wins, and the checkpoint is used by the next pull', () async {
      final r = await rig();
      r.b.feedPages = [
        [item('document', 'd1', 1), item('transaction', 't1', 2)],
        [item('document', 'd1', 3)],
      ];
      final s = await r.sync.pull('org-1', 'co-a');
      expect((s.pages, s.items, s.caughtUp), (2, 3, true));
      expect(r.b.feedCursors, ['', 'C1']);
      r.b.feedCursors.clear();
      await r.sync.pull('org-1', 'co-a');
      expect(r.b.feedCursors, [
        'C2',
      ], reason: 'continues from the saved cursor');
      final v = await r.sync.status('org-1', 'co-a');
      expect(v.knownItems, {'document': 1, 'transaction': 1});
      expect(v.state, SyncState.synchronized);
    });

    test('a failed page records the failure, keeps the earlier page, and is never reported as synchronized', () async {
      final r = await rig();
      r.b.feedPages = [
        [item('document', 'd1', 1)],
        [item('document', 'd2', 1)],
      ];
      await r.sync.pull('org-1', 'co-a');
      r.b.feedFails = true;
      r.b.feedPages = [
        [item('document', 'd1', 1)],
        [item('document', 'd2', 1)],
        [item('document', 'd3', 1)],
      ];
      await expectLater(r.sync.pull('org-1', 'co-a'), throwsA(isA<ApiError>()));
      final v = await r.sync.status('org-1', 'co-a');
      expect(v.state, SyncState.failed);
      expect(v.lastError, 'unavailable');
      expect(v.knownItems['document'], 2, reason: 'what was stored is kept');
    });

    test('a refused cursor restarts from the beginning once', () async {
      final r = await rig();
      r.b.feedPages = [
        [item('document', 'd1', 1)],
      ];
      await r.sync.pull('org-1', 'co-a');
      r.b.badCursor = true;
      r.b.feedCursors.clear();
      final s = await r.sync.pull('org-1', 'co-a');
      expect(s.restarted, isTrue);
      expect(r.b.feedCursors, [
        'C1',
        '',
      ], reason: 'the refused cursor, then a fresh start');
    });

    test('if the server cannot be asked about conflicts the answer is "pending", never "synchronized"', () async {
      final r = await rig();
      r.b.feedPages = [[]];
      await r.sync.pull('org-1', 'co-a');
      expect(
        (await r.sync.status('org-1', 'co-a')).state,
        SyncState.synchronized,
      );
      r.b.syncStatusFails = true;
      final v = await r.sync.status('org-1', 'co-a');
      expect((v.state, v.openConflicts), (SyncState.pending, null));
    });
  });

  group('conflicts', () {
    test('are counted by the server; a decision is the server\'s to accept, and a locked record cannot take a local change', () async {
      final r = await rig();
      r.b.feedPages = [[]];
      await r.sync.pull('org-1', 'co-a');
      r.b.conflicts.addAll([conflict('c1'), conflict('c2', type: 'locked')]);
      expect(
        (await r.sync.status('org-1', 'co-a')).state,
        SyncState.conflictDetected,
      );
      final open = await r.sync.openConflicts('org-1', 'co-a');
      expect(open.map((c) => c.isLocked), [false, true]);
      final locked = open.last;
      await expectLater(
        r.sync.resolve(
          'org-1',
          'co-a',
          locked.id,
          'apply_local',
          note: 'force it through',
        ),
        throwsA(isA<ApiError>().having((e) => e.code, 'code', 'locked')),
      );
      await expectLater(
        r.sync.resolve('org-1', 'co-a', open.first.id, 'apply_local'),
        throwsA(isA<ApiError>().having((e) => e.code, 'code', 'note_required')),
      );
      final ok = await r.sync.resolve(
        'org-1',
        'co-a',
        open.first.id,
        'apply_local',
        note: 'My edit is right',
      );
      expect(
        ok.nextBaseRevision,
        4,
        reason: 'the change must now be sent on top of the server revision',
      );
      final kept = await r.sync.resolve(
        'org-1',
        'co-a',
        locked.id,
        'keep_server',
      );
      expect(kept.nextBaseRevision, isNull);
      expect(
        (await r.sync.status('org-1', 'co-a')).state,
        SyncState.synchronized,
      );
    });

    test('the screen state helpers never throw: a failing server becomes a message', () async {
      final r = await rig(backend: FakeBackend()..online = false);
      await r.sync.refresh('org-1', 'co-a');
      expect(r.sync.message, contains('cannot be reached'));
    });
  });

  group('resumable uploads', () {
    Future<(Rig, QueueItem, UploadService)> big({int size = 9 * mib}) async {
      final r = await rig();
      final f = File('${r.dir.path}/big.pdf')
        ..writeAsBytesSync(List<int>.generate(size, (i) => (i * 31 + 7) % 251));
      final item = await r.queue.enqueue(
        sourcePath: f.path,
        organizationId: 'org-1',
        companyId: 'co-a',
        nodeId: 'n-co-a-0',
        metadata: const {},
        source: UploadSource.file,
      );
      return (r, item, UploadService(r.api, r.queue));
    }

    test('a big file goes in chunks; when an answer is lost the next try continues from what the server holds', () async {
      final (r, item, svc) = await big();
      r.b.dropChunkAnswerAt =
          4 * mib; // chunk 2 is stored, but the phone never hears about it
      await svc.run(item);
      expect(
        item.state,
        isNot(UploadState.completed),
        reason: 'never shown as uploaded before the server confirmed it',
      );
      expect(r.b.chunkCalls.map((c) => c.$1), [0, 4 * mib]);
      r.b.chunkCalls.clear();
      await svc.run(item);
      expect(r.b.chunkCalls, [
        (8 * mib, 1 * mib),
      ], reason: 'only the missing tail is sent');
      expect(item.state, UploadState.completed);
      expect(r.b.storedDocuments, 1);
    });

    test('a server that lost its staging is believed: the phone restarts from the offset the server names', () async {
      final (r, item, svc) = await big();
      r.b.forgetBytesAt = 4 * mib;
      await svc.run(item);
      expect(r.b.chunkCalls.map((c) => c.$1), [
        0,
        0,
        4 * mib,
        8 * mib,
      ], reason: 'after the correction it sends everything again from 0');
      expect(item.state, UploadState.completed);
    });

    test('small files still use the single request', () async {
      final (r, item, svc) = await big(size: 2048);
      await svc.run(item);
      expect(r.b.chunkCalls, isEmpty);
      expect(r.b.putCalls, 1);
      expect(item.state, UploadState.completed);
    });
  });

  group('server notifications', () {
    test('the person\'s own notifications appear with generic text; read ones are not unread; marking all read tells the server', () async {
      final r = await rig();
      final prefs = await (() async {
        SharedPreferences.setMockInitialValues({});
        return SharedPreferences.getInstance();
      })();
      r.b.notifications = [
        {
          'id': 'n1',
          'title': 'A bookkeeping task was assigned to you',
          'body': 'Open Daily Bookkeeping to see it.',
          'created_at': '2026-03-02T09:00:00Z',
          'read_at': null,
        },
        {
          'id': 'n2',
          'title': 'A daily closing you prepared was closed',
          'body': '',
          'created_at': '2026-03-01T09:00:00Z',
          'read_at': '2026-03-01T10:00:00Z',
        },
      ];
      r.b.companiesByOrg['org-1']!;
      final nc = NotificationCenter(
        queue: r.queue,
        docs: DocumentsRepository(r.api),
        prefs: prefs,
      );
      await nc.refresh('co-a');
      final list = nc
          .notices('co-a')
          .where((n) => n.kind == NoticeKind.serverNotification)
          .toList();
      expect(list.map((n) => n.title), [
        'A bookkeeping task was assigned to you',
        'A daily closing you prepared was closed',
      ]);
      expect(nc.unread('co-a'), 1);
      await nc.markAllRead('co-a');
      expect(nc.unread('co-a'), 0);
      expect(r.b.notifications.every((n) => n['read_at'] != null), isTrue);
    });
  });
}
