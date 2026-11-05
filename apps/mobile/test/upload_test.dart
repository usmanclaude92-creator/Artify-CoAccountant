import 'dart:io';
import 'dart:typed_data';

import 'package:artify_mobile/core/config/app_config.dart';
import 'package:artify_mobile/core/network/api_client.dart';
import 'package:artify_mobile/core/platform/platform_services.dart';
import 'package:artify_mobile/core/storage/secure_store.dart';
import 'package:artify_mobile/features/documents/documents_repository.dart';
import 'package:artify_mobile/features/notifications/notification_center.dart';
import 'package:artify_mobile/features/upload/queue_runner.dart';
import 'package:artify_mobile/features/upload/upload_models.dart';
import 'package:artify_mobile/features/upload/upload_queue.dart';
import 'package:artify_mobile/features/upload/upload_service.dart';
import 'package:artify_mobile/features/upload/upload_validator.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'support/fake_backend.dart';

class Rig {
  Rig(this.b, this.api, this.dir, this.queue, this.service, this.net, this.runner, this.src);
  final FakeBackend b;
  final ApiClient api;
  final Directory dir;
  final UploadQueue queue;
  final UploadService service;
  final FakeConnectivity net;
  final QueueRunner runner;
  final Directory src;
}

Future<Rig> rig({FakeBackend? backend, Uint8List? now}) async {
  final b = backend ?? FakeBackend();
  b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 1, names: ['Finance']);
  final tokens = TokenStore(MemorySecureStore());
  await tokens.save(Session(accessToken: 'a', refreshToken: 'r', expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1))));
  final api = ApiClient(config: const AppConfig(apiBaseUrl: 'https://api.test/api/v1'), tokens: tokens, client: b.client)..organizationId = 'org-1';
  final dir = await Directory.systemTemp.createTemp('queue');
  final src = await Directory.systemTemp.createTemp('src');
  final queue = UploadQueue(dir);
  final service = UploadService(api, queue);
  final net = FakeConnectivity(true);
  final runner = QueueRunner(queue: queue, service: service, connectivity: net, autoSchedule: false);
  return Rig(b, api, dir, queue, service, net, runner, src);
}

Future<QueueItem> add(Rig r, {String name = 'statement.pdf', List<int>? bytes, String company = 'co-a', String node = 'n-co-a-0', bool draft = false}) async {
  final f = File('${r.src.path}/$name')..writeAsBytesSync(bytes ?? List<int>.generate(2048, (i) => (i * 7 + name.length) % 251));
  return r.queue.enqueue(sourcePath: f.path, organizationId: 'org-1', companyId: company, nodeId: node, metadata: const {'month': '2026-02-28'}, source: UploadSource.scan, pages: 3, asDraft: draft);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('validation before queueing', () {
    test('unsupported types, empty files, category type lists and size limits are caught on the phone; the server still has the last word', () {
      expect(UploadValidator.check('a.pdf', 10).isOk, isTrue);
      expect(UploadValidator.check('a.PDF', 10).isOk, isTrue);
      expect(UploadValidator.check('a.exe', 10).problem, contains('not accepted'));
      expect(UploadValidator.check('noext', 10).problem, contains('no type'));
      expect(UploadValidator.check('a.pdf', 0).problem, contains('empty'));
      expect(UploadValidator.check('a.pdf', 20 * 1048576, maxBytes: 10 * 1048576).problem, contains('larger'));
      expect(UploadValidator.check('a.png', 10, allowedForCategory: ['pdf']).problem, contains('accepts only'));
      expect(UploadValidator.check('photo.heic', 10).isOk, isTrue);
      expect(UploadValidator.mimeOf('a.jpg'), 'image/jpeg');
    });
  });

  group('the queue on disk', () {
    test('enqueue copies the file privately, records its hash and rejects what cannot work', () async {
      final r = await rig();
      final i = await add(r);
      expect(File(i.localPath).existsSync(), isTrue);
      expect(i.localPath.startsWith(r.dir.path), isTrue, reason: 'inside the app\'s own storage');
      expect(i.sha256, hasLength(64));
      expect(i.state, UploadState.waiting);
      final bad = File('${r.src.path}/virus.exe')..writeAsBytesSync([1, 2, 3]);
      await expectLater(r.queue.enqueue(sourcePath: bad.path, organizationId: 'org-1', companyId: 'co-a', nodeId: 'n'), throwsA(isA<EnqueueRejected>()));
      final empty = File('${r.src.path}/empty.pdf')..writeAsBytesSync([]);
      await expectLater(r.queue.enqueue(sourcePath: empty.path, organizationId: 'org-1', companyId: 'co-a', nodeId: 'n'), throwsA(isA<EnqueueRejected>()));
    });

    test('the same file for the same destination cannot be queued twice, but another destination or company can', () async {
      final r = await rig();
      await add(r);
      await expectLater(add(r), throwsA(isA<EnqueueRejected>().having((e) => e.message, 'message', contains('already waiting'))));
      await add(r, node: 'n-co-a-9');
      await add(r, company: 'co-b');
      expect(r.queue.items.length, 3);
    });

    test('closing and reopening the app keeps every item; one that was mid-upload goes back to waiting', () async {
      final r = await rig();
      final a = await add(r, name: 'one.pdf', bytes: List.filled(500, 1));
      final b = await add(r, name: 'two.pdf', bytes: List.filled(500, 2));
      a.state = UploadState.uploading;
      a.serverOperationId = 'op-1';
      b.state = UploadState.verifying;
      await r.queue.update(a);
      await r.queue.update(b);
      final reopened = UploadQueue(r.dir);
      await reopened.load();
      expect(reopened.items.map((e) => e.fileName), ['one.pdf', 'two.pdf']);
      expect(reopened.items.every((e) => e.state == UploadState.waiting), isTrue);
      expect(reopened.items.first.idempotencyKey, a.idempotencyKey, reason: 'the retry reuses the key, so no duplicate document can result');
      expect(reopened.items.first.metadata, {'month': '2026-02-28'});
    });

    test('cancelling stops the upload and removes the local copy; a draft waits until the person submits it', () async {
      final r = await rig();
      final i = await add(r);
      await r.queue.cancel(i.id);
      expect(i.state, UploadState.cancelled);
      expect(File(i.localPath).existsSync(), isFalse);
      final d = await add(r, name: 'draft.pdf', draft: true);
      expect(d.state, UploadState.draft);
      await r.runner.kick();
      expect(r.b.log.where((l) => l.contains('/documents/uploads')), isEmpty, reason: 'drafts and cancelled items are never sent');
      await r.queue.submitDraft(d.id);
      expect(d.state, UploadState.waiting);
    });

    test('a damaged journal is kept aside and does not crash the app', () async {
      final r = await rig();
      await add(r);
      File('${r.dir.path}/queue.json').writeAsStringSync('[ nope');
      final again = UploadQueue(r.dir);
      await again.load();
      expect(again.items, isEmpty);
      expect(File('${r.dir.path}/queue.damaged.json').existsSync(), isTrue);
    });
  });

  group('uploading', () {
    test('success: register, send, complete; "verified" only after the server confirms; the local copy is removed', () async {
      final r = await rig();
      final i = await add(r);
      await r.service.run(i);
      expect(r.b.log.where((l) => l.contains('/documents/uploads')).toList(), [
        'POST /companies/co-a/documents/uploads',
        'PUT /companies/co-a/documents/uploads/op-1/content',
        'POST /companies/co-a/documents/uploads/op-1/complete',
      ]);
      expect(i.state, UploadState.completed);
      expect(i.documentId, 'doc-1');
      expect(File(i.localPath).existsSync(), isFalse);
      expect(i.stageLabel(online: true), 'Verified in cloud storage');
      expect(r.b.opsByKey[i.idempotencyKey]!['metadata'], {'month': '2026-02-28'});
    });

    test('the company and organization of the ITEM are used, not whatever is selected now', () async {
      final r = await rig();
      r.api.organizationId = 'org-2';
      r.b.companiesByOrg['org-2'] = [{'id': 'co-z', 'name': 'Z', 'code': 'Z', 'status': 'active', 'country': 'OM', 'base_currency': 'OMR', 'assigned_to_me': false}];
      final i = await add(r);
      await r.service.run(i);
      final init = r.b.log.indexOf('POST /companies/co-a/documents/uploads');
      expect(r.b.headersSeen[init]['X-Organization-Id'], 'org-1');
    });

    test('an interrupted upload stays queued with a back-off; the retry reuses the idempotency key and stores exactly one document', () async {
      final r = await rig();
      final i = await add(r);
      r.b.failNextPut = true;
      await r.service.run(i);
      expect(i.state, UploadState.waiting);
      expect(i.errorCode, 'network_unavailable');
      expect(i.attempts, 1);
      expect(i.nextAttemptAt, isNotNull);
      expect(File(i.localPath).existsSync(), isTrue, reason: 'the original is preserved until the server confirms');
      i.nextAttemptAt = null;
      await r.service.run(i);
      expect(i.state, UploadState.completed);
      expect(r.b.initCalls, 2);
      expect(r.b.opsByKey.length, 1, reason: 'the second registration returned the first operation');
      expect(r.b.storedDocuments, 1, reason: 'no duplicate document');
    });

    test('a refusal from the server (wrong type, destination, validation) fails the item with the server\'s words and does not retry', () async {
      final r = await rig(backend: FakeBackend()..initStatus = 422..initProblem = {'code': 'file_not_accepted', 'detail': 'Files of type .pdf are not accepted in this folder.'});
      final i = await add(r);
      await r.service.run(i);
      expect(i.state, UploadState.failed);
      expect(i.retryable, isFalse);
      expect(i.errorMessage, 'Files of type .pdf are not accepted in this folder.');
      r.b.initStatus = null;
      await r.queue.retry(i.id);
      await r.service.run(i);
      expect(i.state, UploadState.completed, reason: 'after the person fixed the cause, a manual retry works');
    });

    test('a plan limit shows the limit and sends the person to the administrator', () async {
      final r = await rig(backend: FakeBackend()..initStatus = 402..initProblem = {'code': 'entitlement_exceeded', 'detail': 'Limit quota.storage_bytes reached.'});
      final i = await add(r);
      await r.service.run(i);
      expect(i.state, UploadState.failed);
      expect(i.errorMessage, contains('administrator'));
    });

    test('a file the server quarantines is reported as quarantined and is not retried', () async {
      final r = await rig(backend: FakeBackend()..putStatus = 422..putProblem = {'code': 'infected', 'detail': 'The file was held for safety.', 'operation': {'status': 'quarantined'}});
      final i = await add(r);
      await r.service.run(i);
      expect(i.state, UploadState.quarantined);
      await r.queue.retry(i.id);
      expect(i.state, UploadState.quarantined);
    });

    test('"completed" is never claimed unless the server confirms it', () async {
      final r = await rig(backend: FakeBackend()..completeNotConfirmed = true);
      final i = await add(r);
      await r.service.run(i);
      expect(i.state, UploadState.waiting);
      expect(i.documentId, isNull);
      expect(File(i.localPath).existsSync(), isTrue);
      r.b.completeNotConfirmed = false;
      i.nextAttemptAt = null;
      await r.service.run(i);
      expect(i.state, UploadState.completed);
      expect(r.b.storedDocuments, 1);
    });

    test('back-off grows with each failed attempt and is capped', () async {
      final r = await rig(backend: FakeBackend()..online = false);
      final i = await add(r);
      final delays = <Duration>[];
      for (var n = 0; n < 12; n++) {
        i.nextAttemptAt = null;
        final before = DateTime.now().toUtc();
        await r.service.run(i);
        delays.add(i.nextAttemptAt!.difference(before));
      }
      expect(delays[1], greaterThan(delays[0]));
      expect(delays.last, lessThanOrEqualTo(UploadService.maxBackoff + const Duration(seconds: 2)));
    });

    test('a missing local file is a clear failure, not a crash', () async {
      final r = await rig();
      final i = await add(r);
      File(i.localPath).deleteSync();
      await r.service.run(i);
      expect(i.state, UploadState.failed);
      expect(i.errorMessage, contains('no longer on the device'));
    });
  });

  group('offline capture and the runner', () {
    test('capture offline -> stays saved on the device -> uploads on its own when the connection returns', () async {
      final r = await rig();
      r.net.online = false;
      r.b.online = false;
      final i = await add(r);
      await r.runner.start();
      expect(i.state, UploadState.waiting);
      expect(i.stageLabel(online: false), 'Saved on this device — waiting for a connection');
      expect(r.b.log, isEmpty, reason: 'nothing is attempted while offline');
      r.b.online = true;
      r.net.set(true);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      for (var n = 0; n < 40 && i.state != UploadState.completed; n++) {
        await Future<void>.delayed(const Duration(milliseconds: 25));
      }
      expect(i.state, UploadState.completed);
      expect(i.stageLabel(online: true), 'Verified in cloud storage');
    });

    test('items upload one at a time in the order they were queued, and an item that must wait does not block the others', () async {
      final r = await rig();
      final a = await add(r, name: 'a.pdf', bytes: List.filled(300, 1));
      final b = await add(r, name: 'b.pdf', bytes: List.filled(300, 2));
      a.nextAttemptAt = DateTime.now().toUtc().add(const Duration(hours: 1));
      await r.queue.update(a);
      await r.runner.kick();
      expect(b.state, UploadState.completed);
      expect(a.state, UploadState.waiting);
    });

    test('an expired session pauses the runner instead of failing the documents; they continue after sign-in', () async {
      final r = await rig(backend: FakeBackend()..refreshWorks = false);
      final i = await add(r);
      r.b.accessExpired = true;
      await r.runner.kick();
      expect(i.state, UploadState.waiting);
      expect(i.errorMessage, contains('Sign in'));
      expect(r.runner.paused, isTrue);
      // sign in again
      await r.api.tokens.save(Session(accessToken: 'new', refreshToken: 'r2', expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1))));
      r.b.refreshWorks = true;
      await r.runner.resume();
      expect(i.state, UploadState.completed);
    });

    test('a restart with pending items resumes them', () async {
      final r = await rig();
      r.net.online = false;
      final i = await add(r);
      expect(i.state, UploadState.waiting);
      final queue2 = UploadQueue(r.dir);
      final runner2 = QueueRunner(queue: queue2, service: UploadService(r.api, queue2), connectivity: FakeConnectivity(true), autoSchedule: false);
      await runner2.start();
      expect(queue2.items.single.state, UploadState.completed);
      expect(r.b.storedDocuments, 1);
    });
  });

  group('notices', () {
    test('built from real queue records and server events, in generic wording, with read state and preferences', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final r = await rig();
      final ok = await add(r, name: 'ok.pdf', bytes: List.filled(200, 1));
      await r.service.run(ok);
      r.b.initStatus = 422;
      r.b.initProblem = {'code': 'x', 'detail': 'Not accepted here.'};
      final bad = await add(r, name: 'bad.pdf', bytes: List.filled(200, 2));
      await r.service.run(bad);
      r.b.events.addAll([
        {'id': 'e1', 'event_type': 'processing.failed', 'created_at': '2026-03-02T10:00:00Z', 'document_id': 'doc-1', 'summary': {'amount': '9999.00 OMR'}},
        {'id': 'e2', 'event_type': 'processing.completed', 'created_at': '2026-03-02T11:00:00Z', 'document_id': 'doc-1'},
        {'id': 'e3', 'event_type': 'document.viewed', 'created_at': '2026-03-02T12:00:00Z', 'document_id': 'doc-1'},
      ]);
      final center = NotificationCenter(queue: r.queue, docs: DocumentsRepository(r.api), prefs: prefs);
      await center.refresh('co-a');
      final list = center.notices('co-a');
      expect(list.map((n) => n.title), containsAll(['Upload verified', 'Upload failed', 'Processing failed', 'Processing completed']));
      expect(list.any((n) => n.title.contains('viewed')), isFalse, reason: 'only events a person needs to act on');
      expect(list.map((n) => '${n.title} ${n.detail}').join(' '), isNot(contains('9999')), reason: 'no amounts in notification text');
      expect(center.unread('co-a'), 4);
      await center.markAllRead('co-a');
      expect(center.unread('co-a'), 0);
      await center.setShowServerNotices(false);
      expect(center.notices('co-a').map((n) => n.title), isNot(contains('Processing failed')));
    });

    test('with the server unreachable the device notices stay and the reason is stated', () async {
      SharedPreferences.setMockInitialValues({});
      final r = await rig();
      final center = NotificationCenter(queue: r.queue, docs: DocumentsRepository(r.api), prefs: await SharedPreferences.getInstance());
      r.b.online = false;
      await center.refresh('co-a');
      expect(center.error, contains('cannot be reached'));
    });
  });

  group('documents', () {
    test('history is paged, comes from the server, and a document the server hides is simply absent', () async {
      final r = await rig();
      for (var n = 0; n < 5; n++) {
        await r.service.run(await add(r, name: 'doc$n.pdf', bytes: List.filled(100 + n, n)));
      }
      final docs = DocumentsRepository(r.api);
      final p1 = await docs.list('co-a', limit: 2);
      final p2 = await docs.list('co-a', limit: 2, offset: 2);
      expect(p1.total, 5);
      expect(p1.items.length, 2);
      expect([...p1.items, ...p2.items].map((d) => d.id).toSet().length, 4);
      expect(p1.items.first.createdByName, 'Sara Preparer');
      expect(r.b.log.any((l) => l.contains('/documents') && l.startsWith('GET')), isTrue);
    });
  });
}
