import 'dart:io';

import 'package:artify_mobile/core/config/app_config.dart';
import 'package:artify_mobile/core/network/api_client.dart';
import 'package:artify_mobile/core/network/api_error.dart';
import 'package:artify_mobile/core/storage/secure_store.dart';
import 'package:artify_mobile/features/workspace/metadata_validator.dart';
import 'package:artify_mobile/features/workspace/workspace_models.dart';
import 'package:artify_mobile/features/workspace/workspace_repository.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_backend.dart';

Future<(FakeBackend, WorkspaceRepository, Directory)> setup() async {
  final b = FakeBackend();
  final store = MemorySecureStore();
  final tokens = TokenStore(store);
  await tokens.save(Session(accessToken: 'a', refreshToken: 'r', expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1))));
  final api = ApiClient(config: const AppConfig(apiBaseUrl: 'https://api.test/api/v1'), tokens: tokens, client: b.client)..organizationId = 'org-1';
  final dir = await Directory.systemTemp.createTemp('ws_cache');
  return (b, WorkspaceRepository(api, dir), dir);
}

WsField field(String key, String type, {bool required = false, Map<String, dynamic> validation = const {}, List<Map<String, String>> options = const [], Object? def}) => WsField.fromJson({
      'key': key,
      'label': key,
      'data_type': type,
      'required': required,
      'validation': validation,
      'options': options,
      'default_value': def,
      'category_ids': <String>[],
      'node_ids': <String>[],
    });

void main() {
  group('dynamic workspace', () {
    test('a company with four sections shows four; another with ten shows ten; nothing is hard-coded', () async {
      final (b, repo, _) = await setup();
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 3, names: ['Bank Statements', 'Receivables', 'Payables', 'Expenses']);
      b.uploadConfig['co-b'] = FakeBackend.config('co-b', version: 9, names: ['Finance', 'Sales', 'Purchases', 'Payroll', 'VAT', 'Projects', 'Contracts', 'Reports', 'Assets', 'Legal']);
      final a = (await repo.refresh('co-a')).config, c = (await repo.refresh('co-b')).config;
      expect(a.sections.map((s) => s.label), ['Bank Statements', 'Receivables', 'Payables', 'Expenses']);
      expect(c.sections.length, 10);
      expect(c.sections.map((s) => s.label), isNot(contains('Bank Statements')));
    });

    test('a company with no structure gets an empty list, not defaults', () async {
      final (_, repo, _) = await setup();
      expect((await repo.refresh('co-a')).config.sections, isEmpty);
    });

    test('nested folders are navigable by stable ids, in the company\'s own order, with a breadcrumb path', () async {
      final (b, repo, _) = await setup();
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 1, names: ['Finance', 'Sales'], nested: true);
      final cfg = (await repo.refresh('co-a')).config;
      final finance = cfg.sections.first;
      expect(cfg.children(finance.id).map((t) => t.label), ['Banks']);
      final leaf = cfg.children(cfg.children(finance.id).first.id).single;
      expect(leaf.label, 'Bank of Muscat');
      expect(cfg.pathTo(leaf.id).map((t) => t.label), ['Finance', 'Banks', 'Bank of Muscat']);
      expect(leaf.id, 'n-co-a-c1', reason: 'ids, not names, identify a destination');
    });

    test('ordering follows the position the company set, not the alphabet', () {
      final cfg = UploadConfig.fromJson({
        'company_id': 'c',
        'version': 1,
        'targets': [
          {'id': 'z', 'parent_id': null, 'node_type': 'section', 'key': 'z', 'label': 'Alpha', 'position': 2, 'depth': 1},
          {'id': 'y', 'parent_id': null, 'node_type': 'section', 'key': 'y', 'label': 'Zulu', 'position': 0, 'depth': 1},
        ],
      });
      expect(cfg.sections.map((s) => s.label), ['Zulu', 'Alpha']);
    });

    test('custom categories and metadata fields follow the destination and the category', () {
      final cfg = UploadConfig.fromJson({
        'company_id': 'c',
        'version': 1,
        'targets': [
          {'id': 'fin', 'parent_id': null, 'node_type': 'section', 'key': 'fin', 'label': 'Finance', 'position': 0, 'depth': 1},
          {'id': 'bank', 'parent_id': 'fin', 'node_type': 'folder', 'key': 'bank', 'label': 'Banks', 'position': 0, 'depth': 2},
          {'id': 'hr', 'parent_id': null, 'node_type': 'section', 'key': 'hr', 'label': 'People', 'position': 1, 'depth': 1},
        ],
        'categories': [
          {'id': 'cat-stmt', 'key': 'stmt', 'name': 'Bank statement', 'document_kind': 'bank_statement', 'node_id': 'fin'},
          {'id': 'cat-any', 'key': 'any', 'name': 'Anything', 'document_kind': 'other', 'node_id': null},
          {'id': 'cat-cv', 'key': 'cv', 'name': 'CV', 'document_kind': 'other', 'node_id': 'hr'},
        ],
        'fields': [
          {'key': 'month', 'label': 'Month', 'data_type': 'date', 'required': true, 'category_ids': ['cat-stmt'], 'node_ids': [], 'options': []},
          {'key': 'note', 'label': 'Note', 'data_type': 'short_text', 'required': false, 'category_ids': [], 'node_ids': [], 'options': []},
          {'key': 'grade', 'label': 'Grade', 'data_type': 'dropdown', 'required': false, 'category_ids': [], 'node_ids': ['hr'], 'options': [{'value': 'a', 'label': 'A'}]},
        ],
      });
      expect(cfg.categoriesFor('bank').map((c) => c.name), ['Bank statement', 'Anything'], reason: 'a category on an ancestor applies below it');
      expect(cfg.categoriesFor('hr').map((c) => c.name), ['Anything', 'CV']);
      expect(cfg.fieldsFor('bank', 'cat-stmt').map((f) => f.key), ['month', 'note']);
      expect(cfg.fieldsFor('bank', null).map((f) => f.key), ['note']);
      expect(cfg.fieldsFor('hr', 'cat-cv').map((f) => f.key), ['note', 'grade']);
    });

    test('an unchanged workspace costs a 304 and keeps the cached copy; a changed one is picked up and flagged', () async {
      final (b, repo, _) = await setup();
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 1, names: ['Finance']);
      final first = await repo.refresh('co-a');
      expect(first.changed, isTrue);
      final second = await repo.refresh('co-a');
      expect(second.changed, isFalse);
      expect(b.log.where((l) => l.contains('upload-config')).length, 2);
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 2, names: ['Finance', 'Tax']);
      final third = await repo.refresh('co-a');
      expect(third.changed, isTrue);
      expect(third.config.sections.map((s) => s.label), ['Finance', 'Tax']);
      expect((await repo.cached('co-a'))!.config.version, 2);
    });

    test('offline: the last good copy is available; a different company\'s copy is never mixed in', () async {
      final (b, repo, _) = await setup();
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 1, names: ['A-only section']);
      await repo.refresh('co-a');
      b.online = false;
      await expectLater(repo.refresh('co-a'), throwsA(isA<ApiError>().having((e) => e.isNetwork, 'network', isTrue)));
      expect((await repo.cached('co-a'))!.config.sections.single.label, 'A-only section');
      expect(await repo.cached('co-b'), isNull);
    });

    test('a company the user may not access answers 404 and nothing is cached for it', () async {
      final (b, repo, dir) = await setup();
      b.forbiddenCompanies.add('co-b');
      await expectLater(repo.refresh('co-b'), throwsA(isA<ApiError>().having((e) => e.status, 'status', 404)));
      expect(dir.listSync().where((e) => e.path.contains('co-b')), isEmpty);
    });

    test('a server answer for the wrong company is rejected, never shown', () async {
      final (b, repo, _) = await setup();
      b.uploadConfig['co-a'] = FakeBackend.config('co-b', version: 1, names: ['Not yours']);
      await expectLater(repo.refresh('co-a'), throwsA(isA<ApiError>()));
      expect(await repo.cached('co-a'), isNull);
    });

    test('a damaged cache file is treated as empty instead of crashing', () async {
      final (_, repo, dir) = await setup();
      File('${dir.path}/upload-config-co-a.json').writeAsStringSync('{ not json');
      expect(await repo.cached('co-a'), isNull);
    });
  });

  group('metadata fields', () {
    test('every field type is checked the way its definition says', () {
      final fields = [
        field('ref', 'short_text', required: true, validation: {'max_length': 5}),
        field('qty', 'number', validation: {'min': 1, 'max': 10}),
        field('amount', 'decimal', validation: {'decimal_places': 2}),
        field('paid', 'currency', validation: {'allowed_currencies': ['OMR', 'AED']}),
        field('day', 'date'),
        field('grade', 'dropdown', options: [{'value': 'a', 'label': 'A'}, {'value': 'b', 'label': 'B'}]),
        field('tags', 'multi_select', options: [{'value': 'x', 'label': 'X'}]),
        field('flag', 'boolean'),
        field('link', 'reference'),
      ];
      expect(MetadataValidator.validate(fields, {'ref': 'ok'}), isEmpty);
      final bad = MetadataValidator.validate(fields, {
        'ref': 'too long!',
        'qty': '11',
        'amount': '1.234',
        'paid': {'amount': '5', 'currency': 'USD'},
        'day': 'yesterday',
        'grade': 'z',
        'tags': ['q'],
        'flag': 'yes',
        'link': '',
      });
      expect(bad.keys, containsAll(['ref', 'qty', 'amount', 'paid', 'day', 'grade', 'tags', 'flag']));
      expect(bad.containsKey('link'), isFalse, reason: 'an empty optional field is fine');
      expect(MetadataValidator.validate(fields, {'ref': 'ok', 'qty': '3', 'amount': '12.50', 'paid': {'amount': '5', 'currency': 'OMR'}, 'day': '2026-03-02', 'grade': 'a', 'tags': ['x'], 'flag': true, 'link': 'node-1'}), isEmpty);
    });

    test('required fields must be filled; defaults come from the definition; the payload holds only filled values', () {
      final fields = [field('must', 'short_text', required: true), field('opt', 'short_text'), field('d', 'number', def: '7')];
      expect(MetadataValidator.validate(fields, {})['must'], contains('required'));
      expect(MetadataValidator.defaults(fields), {'d': '7'});
      expect(MetadataValidator.toPayload(fields, {'must': '  hello ', 'opt': '', 'd': '7'}), {'must': 'hello', 'd': '7'});
    });

    test('a field type this version of the app does not know is left to the server, not blocked', () {
      final f = field('future', 'hologram', required: true);
      expect(f.type, FieldType.unknown);
      expect(MetadataValidator.validate([f], {'future': 'x'}), isEmpty);
    });
  });
}
