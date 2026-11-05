/// The company's own workspace as the server describes it for uploading (`GET /companies/{id}/workspace/upload-config`).
/// Nothing here is a default: a company with no sections gets an empty tree, one with ten gets ten. Ids are the stable
/// workspace ids; names are only labels.
class WsTarget {
  const WsTarget({required this.id, required this.parentId, required this.nodeType, required this.key, required this.label, required this.position, required this.depth, this.description = ''});
  final String id;
  final String? parentId;
  final String nodeType;
  final String key;
  final String label;
  final int position;
  final int depth;
  final String description;

  factory WsTarget.fromJson(Map<String, dynamic> j) => WsTarget(
        id: j['id'] as String,
        parentId: j['parent_id'] as String?,
        nodeType: (j['node_type'] as String?) ?? 'folder',
        key: (j['key'] as String?) ?? '',
        label: j['label'] as String,
        position: (j['position'] as num?)?.toInt() ?? 0,
        depth: (j['depth'] as num?)?.toInt() ?? 1,
        description: (j['description'] as String?) ?? '',
      );
}

class WsCategory {
  const WsCategory({required this.id, required this.key, required this.name, required this.documentKind, required this.nodeId, required this.allowedFileTypes, this.description = ''});
  final String id;
  final String key;
  final String name;
  final String documentKind;

  /// When set, the category only exists under this node (and its children).
  final String? nodeId;
  final List<String>? allowedFileTypes;
  final String description;

  factory WsCategory.fromJson(Map<String, dynamic> j) => WsCategory(
        id: j['id'] as String,
        key: (j['key'] as String?) ?? '',
        name: j['name'] as String,
        documentKind: (j['document_kind'] as String?) ?? '',
        nodeId: j['node_id'] as String?,
        allowedFileTypes: (j['allowed_file_types'] as List?)?.cast<String>(),
        description: (j['description'] as String?) ?? '',
      );
}

enum FieldType { shortText, longText, number, decimal, currency, date, dropdown, multiSelect, boolean, reference, unknown }

FieldType fieldTypeOf(String? s) => switch (s) {
      'short_text' => FieldType.shortText,
      'long_text' => FieldType.longText,
      'number' => FieldType.number,
      'decimal' => FieldType.decimal,
      'currency' => FieldType.currency,
      'date' => FieldType.date,
      'dropdown' => FieldType.dropdown,
      'multi_select' => FieldType.multiSelect,
      'boolean' => FieldType.boolean,
      'reference' => FieldType.reference,
      _ => FieldType.unknown,
    };

class WsOption {
  const WsOption(this.value, this.label);
  final String value;
  final String label;
}

class WsField {
  const WsField({required this.key, required this.label, required this.type, required this.required, required this.options, required this.categoryIds, required this.nodeIds, this.description = '', this.defaultValue, this.validation = const {}, this.referenceTarget});
  final String key;
  final String label;
  final FieldType type;
  final bool required;
  final List<WsOption> options;
  final List<String> categoryIds;
  final List<String> nodeIds;
  final String description;
  final Object? defaultValue;
  final Map<String, dynamic> validation;
  final String? referenceTarget;

  factory WsField.fromJson(Map<String, dynamic> j) => WsField(
        key: j['key'] as String,
        label: j['label'] as String,
        type: fieldTypeOf(j['data_type'] as String?),
        required: (j['required'] as bool?) ?? false,
        options: ((j['options'] as List?) ?? []).map((o) => WsOption((o as Map)['value'] as String, o['label'] as String)).toList(),
        categoryIds: ((j['category_ids'] as List?) ?? []).cast<String>(),
        nodeIds: ((j['node_ids'] as List?) ?? []).cast<String>(),
        description: (j['description'] as String?) ?? '',
        defaultValue: j['default_value'],
        validation: ((j['validation'] as Map?) ?? {}).cast<String, dynamic>(),
        referenceTarget: j['reference_target'] as String?,
      );
}

class UploadConfig {
  const UploadConfig({required this.companyId, required this.version, required this.targets, required this.categories, required this.fields});
  final String companyId;
  final int version;
  final List<WsTarget> targets;
  final List<WsCategory> categories;
  final List<WsField> fields;

  factory UploadConfig.fromJson(Map<String, dynamic> j) => UploadConfig(
        companyId: j['company_id'] as String,
        version: (j['version'] as num).toInt(),
        targets: ((j['targets'] as List?) ?? []).map((e) => WsTarget.fromJson(e as Map<String, dynamic>)).toList(),
        categories: ((j['categories'] as List?) ?? []).map((e) => WsCategory.fromJson(e as Map<String, dynamic>)).toList(),
        fields: ((j['fields'] as List?) ?? []).map((e) => WsField.fromJson(e as Map<String, dynamic>)).toList(),
      );

  // ---- tree navigation (ordered by the company's own positions)
  List<WsTarget> children(String? parentId) {
    final list = targets.where((t) => t.parentId == parentId).toList()..sort((a, b) => a.position != b.position ? a.position.compareTo(b.position) : a.label.compareTo(b.label));
    return list;
  }

  List<WsTarget> get sections => children(null);
  WsTarget? byId(String id) => targets.where((t) => t.id == id).firstOrNull;

  /// Root-first path of labels, e.g. `Finance › Bank statements › Bank of Muscat`.
  List<WsTarget> pathTo(String id) {
    final out = <WsTarget>[];
    var cur = byId(id);
    var guard = 0;
    while (cur != null && guard++ < 64) {
      out.insert(0, cur);
      cur = cur.parentId == null ? null : byId(cur.parentId!);
    }
    return out;
  }

  bool _under(String? nodeId, String ancestor) => nodeId != null && pathTo(nodeId).any((t) => t.id == ancestor);

  /// Categories a document filed in [nodeId] may use: company-wide ones, and ones attached to this node or an ancestor.
  List<WsCategory> categoriesFor(String nodeId) => categories.where((c) => c.nodeId == null || _under(nodeId, c.nodeId!)).toList();

  /// Fields to ask for when the document goes to [nodeId] with [categoryId] (a field with no scope applies everywhere).
  List<WsField> fieldsFor(String nodeId, String? categoryId) => fields.where((f) {
        final byCat = f.categoryIds.isEmpty || (categoryId != null && f.categoryIds.contains(categoryId));
        final byNode = f.nodeIds.isEmpty || f.nodeIds.any((n) => _under(nodeId, n));
        return byCat && byNode;
      }).toList();
}
