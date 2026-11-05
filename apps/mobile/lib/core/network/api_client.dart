import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../config/app_config.dart';
import '../storage/secure_store.dart';
import 'api_error.dart';

/// HTTP access to the Artify API. It adds the session, refreshes it once when the server says it has expired, and turns
/// every failure into an [ApiError]. The active organization is sent in `X-Organization-Id`; the SERVER decides whether
/// the user may act in it (the app never trusts its own selection).
class ApiClient {
  ApiClient({
    required this.config,
    required this.tokens,
    http.Client? client,
    this.onSessionExpired,
    this.timeout = const Duration(seconds: 30),
  }) : _http = client ?? http.Client();

  final AppConfig config;
  final TokenStore tokens;
  final http.Client _http;
  final void Function()? onSessionExpired;
  final Duration timeout;

  /// Set by the app when an organization is selected.
  String? organizationId;

  Completer<bool>? _refreshing;

  Map<String, String> _headers(Session? s, {bool json = true, Map<String, String>? extra}) => {
        'Accept': 'application/json',
        if (json) 'Content-Type': 'application/json',
        if (s != null) 'Authorization': 'Bearer ${s.accessToken}',
        'X-Organization-Id': ?organizationId,
        ...?extra,
      };

  /// [org] overrides the active organization for this one call (the upload queue may hold items of another organization).
  Future<Map<String, dynamic>> getJson(String path, {Map<String, String>? query, bool auth = true, String? org}) async =>
      _decode(await _send('GET', path, query: query, auth: auth, extra: _orgHeader(org)));

  static Map<String, String>? _orgHeader(String? org) => org == null ? null : {'X-Organization-Id': org};

  Future<List<dynamic>> getList(String path, {Map<String, String>? query}) async {
    final r = await _send('GET', path, query: query);
    final v = jsonDecode(utf8.decode(r.bodyBytes));
    return v is List ? v : <dynamic>[];
  }

  Future<Map<String, dynamic>> postJson(String path, {Object? body, bool auth = true, Map<String, String>? headers, String? org}) async =>
      _decode(await _send('POST', path, body: body == null ? null : jsonEncode(body), auth: auth, extra: {...?headers, ...?_orgHeader(org)}));

  Future<Map<String, dynamic>> patchJson(String path, Object body) async =>
      _decode(await _send('PATCH', path, body: jsonEncode(body)));

  Future<Map<String, dynamic>> putJson(String path, Object body) async =>
      _decode(await _send('PUT', path, body: jsonEncode(body)));

  Future<Map<String, dynamic>> deleteJson(String path) async => _decode(await _send('DELETE', path));

  /// `GET` that exposes the ETag/304 contract used by the workspace configuration.
  Future<ConditionalResponse> getConditional(String path, {String? etag}) async {
    final r = await _send('GET', path, extra: {'If-None-Match': ?etag}, allow304: true);
    if (r.statusCode == 304) return const ConditionalResponse(notModified: true);
    return ConditionalResponse(body: _decode(r), etag: r.headers['etag']);
  }

  /// Raw bytes (document content, page images).
  Future<Uint8List> getBytes(String path, {String? org}) async => (await _send('GET', path, json: false, extra: _orgHeader(org))).bodyBytes;

  /// `PUT` of a file body (the upload protocol's content step).
  Future<Map<String, dynamic>> putBytes(String path, Uint8List bytes, {Duration? timeout, String? org}) async =>
      _decode(await _send('PUT', path, bodyBytes: bytes, json: false, extra: {'Content-Type': 'application/octet-stream', ...?_orgHeader(org)}, timeout: timeout ?? const Duration(minutes: 5)));

  /// `PATCH` of one chunk of a resumable upload (`?offset=` says where it belongs; the server answers 409 `offset_mismatch`
  /// with the offset it really holds when the two disagree).
  Future<Map<String, dynamic>> patchBytes(String path, Uint8List bytes, {Map<String, String>? query, Duration? timeout, String? org}) async =>
      _decode(await _send('PATCH', path, query: query, bodyBytes: bytes, json: false, extra: {'Content-Type': 'application/octet-stream', ...?_orgHeader(org)}, timeout: timeout ?? const Duration(minutes: 5)));

  Map<String, dynamic> _decode(http.Response r) {
    if (r.bodyBytes.isEmpty) return <String, dynamic>{};
    final v = jsonDecode(utf8.decode(r.bodyBytes));
    return v is Map<String, dynamic> ? v : <String, dynamic>{'items': v};
  }

  Future<http.Response> _send(
    String method,
    String path, {
    Map<String, String>? query,
    String? body,
    Uint8List? bodyBytes,
    bool auth = true,
    bool json = true,
    Map<String, String>? extra,
    bool allow304 = false,
    Duration? timeout,
    bool retried = false,
  }) async {
    Session? s;
    if (auth) {
      s = await tokens.load();
      if (s == null) {
        onSessionExpired?.call();
        throw ApiError.sessionExpired();
      }
      if (s.expiresAt.isBefore(DateTime.now().toUtc().add(const Duration(seconds: 20))) && !retried) {
        if (await _refresh()) s = await tokens.load();
      }
    }
    final req = http.Request(method, config.uri(path, query))..headers.addAll(_headers(s, json: json && bodyBytes == null, extra: extra));
    if (body != null) req.body = body;
    if (bodyBytes != null) req.bodyBytes = bodyBytes;
    http.Response res;
    try {
      res = await http.Response.fromStream(await _http.send(req).timeout(timeout ?? this.timeout));
    } on TimeoutException {
      throw ApiError.network();
    } on http.ClientException {
      throw ApiError.network();
    } on Exception {
      throw ApiError.network();
    }
    if (res.statusCode == 401 && auth && !retried) {
      if (await _refresh()) {
        return _send(method, path, query: query, body: body, bodyBytes: bodyBytes, auth: auth, json: json, extra: extra, allow304: allow304, timeout: timeout, retried: true);
      }
      await tokens.clearSession();
      onSessionExpired?.call();
      throw ApiError.sessionExpired();
    }
    if (res.statusCode == 304 && allow304) return res;
    if (res.statusCode >= 200 && res.statusCode < 300) return res;
    Map<String, dynamic>? problem;
    try {
      final v = jsonDecode(utf8.decode(res.bodyBytes));
      if (v is Map<String, dynamic>) problem = v;
    } on FormatException {
      problem = null;
    }
    throw ApiError.fromProblem(res.statusCode, problem);
  }

  /// One refresh at a time; every waiting call shares the outcome.
  Future<bool> _refresh() {
    final running = _refreshing;
    if (running != null) return running.future;
    final c = _refreshing = Completer<bool>();
    () async {
      try {
        final s = await tokens.load();
        if (s == null) return c.complete(false);
        final req = http.Request('POST', config.uri('/auth/refresh'))
          ..headers.addAll({'Content-Type': 'application/json', 'Accept': 'application/json'})
          ..body = jsonEncode({'refresh_token': s.refreshToken});
        final res = await http.Response.fromStream(await _http.send(req).timeout(timeout));
        if (res.statusCode != 200) return c.complete(false);
        final j = jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>;
        await tokens.save(Session.fromTokenJson(j));
        c.complete(true);
      } on Object {
        c.complete(false);
      } finally {
        _refreshing = null;
      }
    }();
    return c.future;
  }

  void close() => _http.close();
}

class ConditionalResponse {
  const ConditionalResponse({this.body, this.etag, this.notModified = false});
  final Map<String, dynamic>? body;
  final String? etag;
  final bool notModified;
}
