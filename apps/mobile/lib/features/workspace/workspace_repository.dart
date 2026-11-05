import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

import '../../core/network/api_client.dart';
import '../../core/network/api_error.dart';
import 'workspace_models.dart';

/// Loads the company's upload configuration and keeps the last good copy for offline use. The copy is keyed by company id,
/// so company A's structure can never appear in company B. Revalidation uses the server's ETag: a changed workspace is
/// picked up by the next refresh, an unchanged one costs a 304.
class WorkspaceRepository {
  WorkspaceRepository(this.api, this.cacheDir);
  final ApiClient api;
  final Directory cacheDir;

  File _file(String companyId) => File(p.join(cacheDir.path, 'upload-config-$companyId.json'));

  Future<CachedConfig?> cached(String companyId) async {
    final f = _file(companyId);
    if (!await f.exists()) return null;
    try {
      final j = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
      return CachedConfig(UploadConfig.fromJson(j['config'] as Map<String, dynamic>), j['etag'] as String?);
    } on Object {
      return null; // a damaged cache is treated as empty
    }
  }

  /// Fresh configuration from the server (or the cached one when the server says nothing changed). Throws [ApiError]
  /// when the server cannot be reached; callers may then fall back to [cached] and say so.
  Future<RefreshResult> refresh(String companyId) async {
    final prior = await cached(companyId);
    final r = await api.getConditional('/companies/$companyId/workspace/upload-config', etag: prior?.etag);
    if (r.notModified && prior != null) return RefreshResult(prior.config, changed: false);
    final cfg = UploadConfig.fromJson(r.body!);
    if (cfg.companyId != companyId) {
      // never show another company's structure, whatever the server sent
      throw const ApiError(status: 500, code: 'unexpected_response', message: 'The server answered for a different company.');
    }
    await cacheDir.create(recursive: true);
    await _file(companyId).writeAsString(jsonEncode({'etag': r.etag, 'config': _toJson(cfg)}));
    return RefreshResult(cfg, changed: prior == null || prior.config.version != cfg.version);
  }

  Future<void> clear() async {
    if (await cacheDir.exists()) {
      await for (final e in cacheDir.list()) {
        if (e is File && p.basename(e.path).startsWith('upload-config-')) await e.delete();
      }
    }
  }

  static Map<String, dynamic> _toJson(UploadConfig c) => {
        'company_id': c.companyId,
        'version': c.version,
        'targets': [for (final t in c.targets) {'id': t.id, 'parent_id': t.parentId, 'node_type': t.nodeType, 'key': t.key, 'label': t.label, 'position': t.position, 'depth': t.depth, 'description': t.description}],
        'categories': [for (final x in c.categories) {'id': x.id, 'key': x.key, 'name': x.name, 'document_kind': x.documentKind, 'node_id': x.nodeId, 'allowed_file_types': x.allowedFileTypes, 'description': x.description}],
        'fields': [
          for (final f in c.fields)
            {
              'key': f.key,
              'label': f.label,
              'data_type': _typeName(f.type),
              'required': f.required,
              'description': f.description,
              'default_value': f.defaultValue,
              'validation': f.validation,
              'reference_target': f.referenceTarget,
              'category_ids': f.categoryIds,
              'node_ids': f.nodeIds,
              'options': [for (final o in f.options) {'value': o.value, 'label': o.label}],
            }
        ],
      };

  static String _typeName(FieldType t) => switch (t) {
        FieldType.shortText => 'short_text',
        FieldType.longText => 'long_text',
        FieldType.number => 'number',
        FieldType.decimal => 'decimal',
        FieldType.currency => 'currency',
        FieldType.date => 'date',
        FieldType.dropdown => 'dropdown',
        FieldType.multiSelect => 'multi_select',
        FieldType.boolean => 'boolean',
        FieldType.reference => 'reference',
        FieldType.unknown => 'unknown',
      };
}

class CachedConfig {
  const CachedConfig(this.config, this.etag);
  final UploadConfig config;
  final String? etag;
}

class RefreshResult {
  const RefreshResult(this.config, {required this.changed});
  final UploadConfig config;
  final bool changed;
}
