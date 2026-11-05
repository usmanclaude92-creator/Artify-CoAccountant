import 'dart:typed_data';

import '../../core/network/api_client.dart';

class DocRow {
  const DocRow({required this.id, required this.displayName, required this.extension, required this.sizeBytes, required this.status, required this.nodeId, required this.categoryId, required this.createdAt, required this.createdByName, required this.currentVersionNo, required this.previewable, required this.rowVersion});
  final String id;
  final String displayName;
  final String extension;
  final int sizeBytes;
  final String status;
  final String nodeId;
  final String? categoryId;
  final DateTime createdAt;
  final String? createdByName;
  final int currentVersionNo;
  final bool previewable;
  final int rowVersion;

  factory DocRow.fromJson(Map<String, dynamic> j) => DocRow(
        id: j['id'] as String,
        displayName: j['display_name'] as String,
        extension: (j['extension'] as String?) ?? '',
        sizeBytes: (j['size_bytes'] as num?)?.toInt() ?? 0,
        status: (j['status'] as String?) ?? 'active',
        nodeId: j['node_id'] as String,
        categoryId: j['category_id'] as String?,
        createdAt: DateTime.parse(j['created_at'] as String),
        createdByName: j['created_by_name'] as String?,
        currentVersionNo: (j['current_version_no'] as num?)?.toInt() ?? 1,
        previewable: (j['previewable'] as bool?) ?? false,
        rowVersion: (j['row_version'] as num?)?.toInt() ?? 1,
      );
}

class DocPage {
  const DocPage(this.items, this.total);
  final List<DocRow> items;
  final int total;
}

/// Documents the server lets THIS user see. The server filters by folder permission; the phone adds nothing to that and
/// never widens it (a hidden folder's documents simply do not arrive).
class DocumentsRepository {
  DocumentsRepository(this.api);
  final ApiClient api;

  Future<DocPage> list(String companyId, {int offset = 0, int limit = 30, String? createdBy, String? query, String? nodeId, bool includeSubfolders = true}) async {
    final j = await api.getJson('/companies/$companyId/documents', query: {
      'limit': '$limit',
      'offset': '$offset',
      'sort': 'created',
      'created_by': ?createdBy,
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
      'node_id': ?nodeId,
      if (nodeId != null) 'include_subfolders': '$includeSubfolders',
    });
    return DocPage([for (final e in (j['items'] as List? ?? [])) DocRow.fromJson(e as Map<String, dynamic>)], (j['total'] as num?)?.toInt() ?? 0);
  }

  Future<DocRow> get(String companyId, String id) async => DocRow.fromJson(await api.getJson('/companies/$companyId/documents/$id'));

  Future<List<Map<String, dynamic>>> versions(String companyId, String id) async {
    final j = await api.getJson('/companies/$companyId/documents/$id/versions');
    return [for (final e in (j['items'] as List? ?? (j['versions'] as List? ?? []))) (e as Map).cast<String, dynamic>()];
  }

  /// Processing status as the document engine reports it; null when nothing has been processed (or the user may not see it).
  Future<Map<String, dynamic>?> processing(String companyId, String id) async {
    try {
      return await api.getJson('/companies/$companyId/documents/$id/intelligence');
    } on Object {
      return null;
    }
  }

  Future<Uint8List> content(String companyId, String id) => api.getBytes('/companies/$companyId/documents/$id/content');

  /// Rename (display name only; the stored original is never changed).
  Future<DocRow> rename(String companyId, DocRow doc, String name) async =>
      DocRow.fromJson(await api.patchJson('/companies/$companyId/documents/${doc.id}', {'display_name': name.trim(), 'base_row_version': doc.rowVersion}));

  Future<Map<String, dynamic>> counts(String companyId) => api.getJson('/companies/$companyId/documents/counts');

  /// Latest document events of the company (the server only returns what the user may see).
  Future<List<Map<String, dynamic>>> events(String companyId, {int limit = 30}) async {
    final j = await api.getJson('/companies/$companyId/documents/events', query: {'limit': '$limit'});
    return [for (final e in (j['items'] as List? ?? [])) (e as Map).cast<String, dynamic>()];
  }
}
