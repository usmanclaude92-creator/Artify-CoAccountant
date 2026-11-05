import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A small stand-in for the Artify API used by the unit tests (synthetic data only). It keeps the state a real server would
/// keep: sessions, uploads by idempotency key, a workspace version.
class FakeBackend {
  FakeBackend() {
    client = MockClient(_handle);
  }
  late final MockClient client;

  // ---- behaviour switches
  bool online = true;
  bool refreshWorks = true;
  String password = 'correct horse battery';
  bool requireMfa = false;
  bool enrollmentRequired = false;
  int accessTokenGeneration = 1;
  bool accessExpired = false; // next authenticated call answers 401 once
  bool failNextPut = false;
  bool completeNotConfirmed = false;
  int? putStatus;
  Map<String, dynamic>? putProblem;
  int? initStatus;
  Map<String, dynamic>? initProblem;
  bool logoutFails = false;
  final Set<String> forbiddenCompanies = {};

  // ---- Phase 12: synchronization, resumable uploads, notifications
  List<List<Map<String, dynamic>>> feedPages = [];
  bool badCursor = false; // the next pull's cursor is refused once
  bool feedFails = false;
  bool syncStatusFails = false;
  final List<Map<String, dynamic>> conflicts = [];
  int chunkMax = 4 * 1024 * 1024;
  final List<(int, int)> chunkCalls = []; // (offset, length)
  int? dropChunkAnswerAt; // apply the chunk at this offset, then lose the answer (connection drops)
  int? forgetBytesAt; // answer 409 offset_mismatch with received_bytes = 0 at this offset (server lost staging)
  List<Map<String, dynamic>> notifications = [];
  bool emailEnabled = true;
  final List<String> feedCursors = [];

  // ---- data
  List<Map<String, dynamic>> memberships = [
    {'organization_id': 'org-1', 'organization_name': 'Gulf Accounting Partners', 'organization_status': 'active', 'organization_role': 'accountant', 'company_roles': <String, String>{}},
  ];
  Map<String, List<Map<String, dynamic>>> companiesByOrg = {
    'org-1': [
      {'id': 'co-a', 'name': 'Al Noor Trading', 'code': 'AN', 'status': 'active', 'country': 'OM', 'base_currency': 'OMR', 'legal_name': 'Al Noor Trading LLC', 'assigned_to_me': true},
      {'id': 'co-b', 'name': 'Gulf Builders', 'code': 'GB', 'status': 'active', 'country': 'OM', 'base_currency': 'OMR', 'legal_name': null, 'assigned_to_me': true},
    ],
  };
  Map<String, Map<String, dynamic>> uploadConfig = {};
  int workspaceVersion = 1;
  final List<Map<String, dynamic>> documents = [];
  final List<Map<String, dynamic>> events = [];

  // ---- observations
  final List<String> log = []; // "METHOD path"
  final List<Map<String, String>> headersSeen = [];
  final Map<String, Map<String, dynamic>> opsByKey = {};
  final Map<String, Map<String, dynamic>> ops = {};
  int storedDocuments = 0;
  int putCalls = 0;
  int initCalls = 0;

  http.Response _json(int status, Object? body, {Map<String, String>? headers}) =>
      http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json', ...?headers});

  http.Response _problem(int status, String code, String detail, {Map<String, dynamic>? extra}) =>
      _json(status, {'type': 'about:blank', 'title': detail, 'status': status, 'code': code, 'detail': detail, ...?extra});

  Future<http.Response> _handle(http.Request r) async {
    if (!online) throw http.ClientException('offline');
    final path = r.url.path.replaceFirst('/api/v1', '');
    log.add('${r.method} $path');
    headersSeen.add(Map.of(r.headers));
    final auth = r.headers['Authorization'] ?? r.headers['authorization'];

    switch ((r.method, path)) {
      case ('POST', '/auth/login'):
        final b = jsonDecode(r.body) as Map<String, dynamic>;
        if (b['password'] != password) return _problem(401, 'invalid_credentials', 'The email or password is not correct.');
        if (enrollmentRequired) return _json(200, {'status': 'ok', 'mfa_enrollment_required': true, 'tokens': null});
        if (requireMfa) return _json(200, {'status': 'mfa_required', 'mfa_token': 'mfa-1', 'tokens': null});
        return _json(200, {'status': 'ok', 'tokens': _tokens()});
      case ('POST', '/auth/mfa/verify'):
        final b = jsonDecode(r.body) as Map<String, dynamic>;
        return b['code'] == '123456' ? _json(200, {'status': 'ok', 'tokens': _tokens()}) : _problem(401, 'invalid_mfa_code', 'That code is not correct.');
      case ('POST', '/auth/refresh'):
        if (!refreshWorks) return _problem(401, 'invalid_refresh_token', 'The session has ended.');
        accessTokenGeneration++;
        accessExpired = false;
        return _json(200, _tokens());
      case ('POST', '/auth/logout'):
        return logoutFails ? _problem(500, 'server_error', 'boom') : http.Response('', 204);
      case ('POST', '/auth/password-reset/request'):
        return http.Response('', 202);
      case ('POST', '/auth/password-reset/confirm'):
        return http.Response('', 204);
    }

    // everything below needs a session
    if (auth == null || accessExpired) {
      accessExpired = false;
      return _problem(401, 'unauthenticated', 'Authentication required');
    }
    final org = r.headers['X-Organization-Id'] ?? r.headers['x-organization-id'];

    if (r.method == 'GET' && path == '/me') {
      return _json(200, {'user_id': 'u-1', 'email': 'sara@example.com', 'display_name': 'Sara Preparer', 'locale': 'en', 'mfa_enabled': true, 'session_mfa_verified': true, 'memberships': memberships, 'permissions': []});
    }
    if (r.method == 'GET' && path == '/notifications') return _json(200, {'items': notifications, 'total': notifications.length, 'unread': notifications.where((n) => n['read_at'] == null).length});
    if (r.method == 'POST' && path == '/notifications/read-all') {
      for (final n in notifications) {
        n['read_at'] ??= '2026-03-02T09:00:00Z';
      }
      return _json(200, {'marked': notifications.length});
    }
    if (r.method == 'GET' && path == '/notifications/preferences') return _json(200, {'email_enabled': emailEnabled});
    if (r.method == 'PUT' && path == '/notifications/preferences') {
      emailEnabled = (jsonDecode(r.body) as Map<String, dynamic>)['email_enabled'] as bool;
      return _json(200, {'email_enabled': emailEnabled});
    }
    if (r.method == 'POST' && path == '/devices') {
      return _json(201, {'id': 'dev-1', 'platform': 'android', 'name': 'Android phone', 'status': 'active'});
    }
    final orgCompanies = RegExp(r'^/orgs/([^/]+)/companies$').firstMatch(path);
    if (r.method == 'GET' && orgCompanies != null) {
      final oid = orgCompanies.group(1)!;
      if (!companiesByOrg.containsKey(oid)) return _problem(404, 'not_found', 'Organization not found.');
      final all = companiesByOrg[oid]!;
      final off = int.parse(r.url.queryParameters['offset'] ?? '0'), lim = int.parse(r.url.queryParameters['limit'] ?? '50');
      return _json(200, {'items': all.skip(off).take(lim).toList(), 'total': all.length, 'limit': lim, 'offset': off});
    }
    final co = RegExp(r'^/companies/([^/]+)/(.*)$').firstMatch(path);
    if (co != null) {
      final cid = co.group(1)!, rest = co.group(2)!;
      if (forbiddenCompanies.contains(cid) || !companiesByOrg.values.any((l) => l.any((c) => c['id'] == cid))) return _problem(404, 'not_found', 'Company not found.');
      if (r.method == 'GET' && rest == 'workspace/upload-config') {
        final cfg = uploadConfig[cid] ?? {'company_id': cid, 'version': workspaceVersion, 'targets': [], 'categories': [], 'fields': []};
        final etag = '"w$cid-${cfg['version']}"';
        if ((r.headers['If-None-Match'] ?? r.headers['if-none-match']) == etag) return http.Response('', 304, headers: {'etag': etag});
        return _json(200, cfg, headers: {'etag': etag});
      }
      if (r.method == 'POST' && rest == 'documents/uploads') {
        initCalls++;
        if (initStatus != null) return _problem(initStatus!, initProblem?['code'] as String? ?? 'unprocessable', initProblem?['detail'] as String? ?? 'Not accepted.', extra: initProblem);
        final b = jsonDecode(r.body) as Map<String, dynamic>;
        final key = b['idempotency_key'] as String;
        final existing = opsByKey[key];
        if (existing != null) return _json(200, existing);
        final op = {'id': 'op-${ops.length + 1}', 'status': 'awaiting_upload', 'original_filename': b['filename'], 'declared_size': b['size'], 'node_id': b['node_id'], 'result_document_id': null, 'idempotency_key': key, 'metadata': b['metadata'], 'chunk_max_bytes': chunkMax, 'received_bytes': 0};
        opsByKey[key] = op;
        ops[op['id'] as String] = op;
        return _json(201, op);
      }
      final put = RegExp(r'^documents/uploads/([^/]+)/content$').firstMatch(rest);
      if (r.method == 'PUT' && put != null) {
        putCalls++;
        if (failNextPut) {
          failNextPut = false;
          throw http.ClientException('connection lost');
        }
        if (putStatus != null) {
          final p = putStatus!;
          putStatus = null;
          return _problem(p, putProblem?['code'] as String? ?? 'unprocessable', putProblem?['detail'] as String? ?? 'Not accepted.', extra: putProblem);
        }
        final op = ops[put.group(1)!]!;
        op['status'] = 'uploaded';
        return _json(200, op);
      }
      final chunk = RegExp(r'^documents/uploads/([^/]+)/chunk$').firstMatch(rest);
      if (r.method == 'PATCH' && chunk != null) {
        final op = ops[chunk.group(1)!]!;
        final offset = int.parse(r.url.queryParameters['offset']!);
        final held = op['received_bytes'] as int;
        if (forgetBytesAt == offset) {
          forgetBytesAt = null;
          op['received_bytes'] = 0;
          return _problem(409, 'offset_mismatch', 'The server holds a different number of bytes.', extra: {'received_bytes': 0, 'declared_size': op['declared_size']});
        }
        if (offset != held) return _problem(409, 'offset_mismatch', 'The server holds a different number of bytes.', extra: {'received_bytes': held, 'declared_size': op['declared_size']});
        chunkCalls.add((offset, r.bodyBytes.length));
        op['received_bytes'] = held + r.bodyBytes.length;
        op['status'] = op['received_bytes'] == op['declared_size'] ? 'uploaded' : 'uploading';
        if (dropChunkAnswerAt == offset) {
          dropChunkAnswerAt = null;
          throw http.ClientException('connection lost after the server stored the chunk');
        }
        return _json(200, op);
      }
      if (r.method == 'GET' && rest == 'sync/changes') {
        if (feedFails) return _problem(503, 'unavailable', 'Try later.');
        final cursor = r.url.queryParameters['cursor'];
        feedCursors.add(cursor ?? '');
        if (badCursor && cursor != null) {
          badCursor = false;
          return _problem(422, 'invalid_cursor', 'That cursor is not valid.');
        }
        final k = cursor == null ? 0 : int.parse(cursor.substring(1));
        final items = k < feedPages.length ? feedPages[k] : <Map<String, dynamic>>[];
        return _json(200, {'items': items, 'next_cursor': 'C${k + 1}', 'has_more': k + 1 < feedPages.length, 'server_time': '2026-03-02T09:00:00Z', 'kinds': ['document']});
      }
      if (r.method == 'GET' && rest == 'sync/status') {
        if (syncStatusFails) return _problem(503, 'unavailable', 'Try later.');
        return _json(200, {'open_conflicts': conflicts.where((c) => c['state'] == 'open').length, 'by_kind': {}, 'server_time': '2026-03-02T09:00:00Z'});
      }
      if (r.method == 'GET' && rest == 'sync/conflicts') {
        return _json(200, {'items': conflicts.where((c) => r.url.queryParameters['state'] == null || c['state'] == r.url.queryParameters['state']).toList(), 'total': conflicts.length});
      }
      final resolve = RegExp(r'^sync/conflicts/([^/]+)/resolve$').firstMatch(rest);
      if (r.method == 'POST' && resolve != null) {
        final c = conflicts.where((x) => x['id'] == resolve.group(1)).firstOrNull;
        if (c == null) return _problem(404, 'not_found', 'Conflict not found.');
        final b = jsonDecode(r.body) as Map<String, dynamic>;
        final res = b['resolution'] as String;
        if ((res == 'apply_local' || res == 'merged') && c['conflict_type'] == 'locked') return _problem(409, 'locked', 'The record is locked; local changes cannot be applied.');
        if ((res == 'apply_local' || res == 'merged') && ((b['note'] as String?) ?? '').trim().length < 5) return _problem(422, 'note_required', 'Explain the decision (at least 5 characters).');
        c['state'] = 'resolved';
        c['resolution'] = res;
        return _json(200, {...c, 'next_base_revision': (res == 'apply_local' || res == 'merged') ? c['server_revision'] : null});
      }
      final done = RegExp(r'^documents/uploads/([^/]+)/complete$').firstMatch(rest);
      if (r.method == 'POST' && done != null) {
        final op = ops[done.group(1)!]!;
        if (completeNotConfirmed) return _json(200, {'operation': {...op, 'status': 'verifying'}, 'document': null});
        if (op['status'] != 'completed') {
          op['status'] = 'completed';
          storedDocuments++;
          op['result_document_id'] = 'doc-$storedDocuments';
          documents.add({'id': 'doc-$storedDocuments', 'display_name': op['original_filename'], 'extension': 'pdf', 'size_bytes': op['declared_size'], 'status': 'active', 'node_id': op['node_id'], 'category_id': null, 'created_at': '2026-03-02T09:00:00Z', 'created_by_name': 'Sara Preparer', 'current_version_no': 1, 'previewable': true, 'row_version': 1});
        }
        return _json(200, {'operation': op, 'document': documents.firstWhere((d) => d['id'] == op['result_document_id'])});
      }
      if (r.method == 'GET' && rest == 'documents') {
        final off = int.parse(r.url.queryParameters['offset'] ?? '0'), lim = int.parse(r.url.queryParameters['limit'] ?? '30');
        return _json(200, {'items': documents.skip(off).take(lim).toList(), 'total': documents.length, 'limit': lim, 'offset': off});
      }
      if (r.method == 'GET' && rest == 'documents/events') return _json(200, {'items': events, 'total': events.length});
      if (r.method == 'GET' && rest == 'documents/counts') return _json(200, {'totals': {'active': documents.length}});
    }
    if (org == null && path.startsWith('/devices')) return _problem(404, 'not_found', 'Organization not found.');
    return _problem(404, 'not_found', 'Not found.');
  }

  Map<String, dynamic> _tokens() => {'access_token': 'access-$accessTokenGeneration', 'refresh_token': 'refresh-$accessTokenGeneration', 'expires_in': 600, 'mfa': true, 'token_type': 'bearer'};

  /// Upload config with `n` top-level sections named by [names]; nested folders under the first one when [nested].
  static Map<String, dynamic> config(String companyId, {required int version, required List<String> names, bool nested = false, List<Map<String, dynamic>> categories = const [], List<Map<String, dynamic>> fields = const []}) {
    final targets = <Map<String, dynamic>>[
      for (var i = 0; i < names.length; i++) {'id': 'n-$companyId-$i', 'parent_id': null, 'node_type': 'section', 'key': 'k$i', 'label': names[i], 'position': i, 'depth': 1, 'description': ''},
      if (nested) ...[
        {'id': 'n-$companyId-c0', 'parent_id': 'n-$companyId-0', 'node_type': 'folder', 'key': 'c0', 'label': 'Banks', 'position': 0, 'depth': 2, 'description': ''},
        {'id': 'n-$companyId-c1', 'parent_id': 'n-$companyId-c0', 'node_type': 'folder', 'key': 'c1', 'label': 'Bank of Muscat', 'position': 0, 'depth': 3, 'description': ''},
      ],
    ];
    return {'company_id': companyId, 'version': version, 'targets': targets, 'categories': categories, 'fields': fields};
  }
}
