import 'dart:convert';

import 'workspace_models.dart';

/// Checks what the person typed against the field definitions the server sent, so mistakes are caught before a photo is
/// queued. The server validates again and has the last word.
class MetadataValidator {
  /// Returns `key -> message` for every problem; an empty map means the values are acceptable.
  static Map<String, String> validate(List<WsField> fields, Map<String, Object?> values) {
    final errors = <String, String>{};
    for (final f in fields) {
      final v = values[f.key];
      final empty = v == null || (v is String && v.trim().isEmpty) || (v is List && v.isEmpty);
      if (empty) {
        if (f.required) errors[f.key] = '${f.label} is required.';
        continue;
      }
      final problem = _check(f, v);
      if (problem != null) errors[f.key] = problem;
    }
    return errors;
  }

  static String? _check(WsField f, Object v) {
    final rules = f.validation;
    switch (f.type) {
      case FieldType.shortText:
      case FieldType.longText:
        final s = v.toString();
        final max = (rules['max_length'] as num?)?.toInt() ?? (f.type == FieldType.shortText ? 200 : 4000);
        final min = (rules['min_length'] as num?)?.toInt();
        if (s.length > max) return '${f.label} can have at most $max characters.';
        if (min != null && s.length < min) return '${f.label} needs at least $min characters.';
        return null;
      case FieldType.number:
      case FieldType.decimal:
      case FieldType.currency:
        final raw = v is Map ? v['amount'] : v;
        final n = num.tryParse(raw.toString().trim());
        if (n == null) return '${f.label} must be a number.';
        if (f.type == FieldType.number && n != n.truncate()) return '${f.label} must be a whole number.';
        final places = (rules['decimal_places'] as num?)?.toInt();
        if (places != null) {
          final frac = raw.toString().trim().split('.');
          if (frac.length > 1 && frac[1].length > places) return '${f.label} can have at most $places decimal places.';
        }
        if (rules['min'] is num && n < (rules['min'] as num)) return '${f.label} must be at least ${rules['min']}.';
        if (rules['max'] is num && n > (rules['max'] as num)) return '${f.label} must be at most ${rules['max']}.';
        if (f.type == FieldType.currency && v is Map) {
          final allowed = (rules['allowed_currencies'] as List?)?.cast<String>();
          final cur = v['currency'] as String?;
          if (cur == null || cur.isEmpty) return 'Choose a currency for ${f.label}.';
          if (allowed != null && !allowed.contains(cur)) return '${f.label} must be in ${allowed.join(', ')}.';
        }
        return null;
      case FieldType.date:
        return DateTime.tryParse(v.toString()) == null ? '${f.label} must be a date.' : null;
      case FieldType.dropdown:
        return f.options.any((o) => o.value == v) ? null : 'Choose one of the listed options for ${f.label}.';
      case FieldType.multiSelect:
        final all = v is List ? v : [v];
        return all.every((x) => f.options.any((o) => o.value == x)) ? null : 'Choose only listed options for ${f.label}.';
      case FieldType.boolean:
        return v is bool ? null : '${f.label} must be yes or no.';
      case FieldType.reference:
        return v is String && v.isNotEmpty ? null : 'Choose a value for ${f.label}.';
      case FieldType.unknown:
        return null; // a type this version of the app does not know: the server decides
    }
  }

  /// Initial values from the definitions' defaults.
  static Map<String, Object?> defaults(List<WsField> fields) => {for (final f in fields) if (f.defaultValue != null) f.key: f.defaultValue};

  /// JSON the server accepts under `metadata` (only fields the person filled in).
  static Map<String, Object?> toPayload(List<WsField> fields, Map<String, Object?> values) {
    final out = <String, Object?>{};
    for (final f in fields) {
      final v = values[f.key];
      if (v == null || (v is String && v.trim().isEmpty) || (v is List && v.isEmpty)) continue;
      out[f.key] = v is String ? v.trim() : v;
    }
    // ensure it is encodable (guards against accidentally storing a widget object)
    jsonEncode(out);
    return out;
  }
}
