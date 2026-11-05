import 'enhance.dart';
import 'geometry.dart';

/// One captured page. The ORIGINAL photo is kept untouched; crop, rotation and clean-up are settings that are applied when
/// the page is rendered, so any of them can be changed again without losing quality.
class ScanPage {
  const ScanPage({required this.id, required this.rawPath, required this.quad, this.rotation = 0, this.adjust = const Adjust(), this.autoDetected = false});
  final String id;
  final String rawPath;
  final Quad quad;

  /// Clockwise quarter turns in degrees: 0, 90, 180, 270.
  final int rotation;
  final Adjust adjust;
  final bool autoDetected;

  ScanPage copyWith({String? rawPath, Quad? quad, int? rotation, Adjust? adjust, bool? autoDetected}) =>
      ScanPage(id: id, rawPath: rawPath ?? this.rawPath, quad: quad ?? this.quad, rotation: rotation ?? this.rotation, adjust: adjust ?? this.adjust, autoDetected: autoDetected ?? this.autoDetected);

  Map<String, dynamic> toJson() => {'id': id, 'raw': rawPath, 'quad': quad.toJson(), 'rotation': rotation, 'adjust': adjust.toJson(), 'auto': autoDetected};
  static ScanPage fromJson(Map<String, dynamic> j) => ScanPage(
        id: j['id'] as String,
        rawPath: j['raw'] as String,
        quad: Quad.fromJson(j['quad'] as Map<String, dynamic>),
        rotation: (j['rotation'] as num?)?.toInt() ?? 0,
        adjust: Adjust.fromJson((j['adjust'] as Map?)?.cast<String, dynamic>() ?? {}),
        autoDetected: (j['auto'] as bool?) ?? false,
      );
}

enum PdfQuality { standard, high }

extension PdfQualityX on PdfQuality {
  /// Long edge in pixels and JPEG quality. "Standard" keeps ordinary invoices and statements legible for OCR at a modest
  /// size; "high" is for small print.
  int get longEdge => this == PdfQuality.high ? 3000 : 2200;
  int get jpegQuality => this == PdfQuality.high ? 92 : 85;
}
