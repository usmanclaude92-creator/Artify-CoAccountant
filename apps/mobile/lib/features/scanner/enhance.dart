import 'dart:typed_data';

import 'package:image/image.dart' as img;

enum ScanMode { color, gray, blackWhite }

class Adjust {
  const Adjust({this.mode = ScanMode.color, this.brightness = 0.0, this.contrast = 0.0, this.removeShadows = false});

  /// -1..1, 0 = unchanged.
  final double brightness;
  final double contrast;
  final ScanMode mode;
  final bool removeShadows;

  bool get isNeutral => mode == ScanMode.color && brightness == 0 && contrast == 0 && !removeShadows;

  Adjust copyWith({ScanMode? mode, double? brightness, double? contrast, bool? removeShadows}) =>
      Adjust(mode: mode ?? this.mode, brightness: brightness ?? this.brightness, contrast: contrast ?? this.contrast, removeShadows: removeShadows ?? this.removeShadows);

  Map<String, dynamic> toJson() => {'mode': mode.name, 'brightness': brightness, 'contrast': contrast, 'shadows': removeShadows};
  static Adjust fromJson(Map<String, dynamic> j) => Adjust(
        mode: ScanMode.values.firstWhere((m) => m.name == j['mode'], orElse: () => ScanMode.color),
        brightness: (j['brightness'] as num?)?.toDouble() ?? 0,
        contrast: (j['contrast'] as num?)?.toDouble() ?? 0,
        removeShadows: (j['shadows'] as bool?) ?? false,
      );
}

/// Document clean-up. Shadow reduction divides each pixel by a heavily blurred copy of the page (the local "paper
/// colour"), which flattens uneven lighting; black-and-white uses a local (adaptive) threshold so text survives in a
/// shadowed corner. Brightness and contrast are applied last. All steps are pure Dart and deterministic.
img.Image enhance(img.Image src, Adjust a) {
  var im = src;
  if (a.removeShadows) im = _flattenIllumination(im);
  switch (a.mode) {
    case ScanMode.color:
      break;
    case ScanMode.gray:
      im = img.grayscale(im.clone());
    case ScanMode.blackWhite:
      return _adaptiveThreshold(im, shift: (a.brightness * 30).round());
  }
  if (a.brightness != 0 || a.contrast != 0) {
    im = img.adjustColor(im.clone(), brightness: 1 + a.brightness * 0.5, contrast: 1 + a.contrast * 0.6);
  }
  return im;
}

img.Image _flattenIllumination(img.Image src) {
  final w = src.width, h = src.height;
  final scale = 64 / (w > h ? w : h);
  if (scale >= 1) return src;
  final tiny = img.copyResize(src, width: (w * scale).round().clamp(2, 64), height: (h * scale).round().clamp(2, 64), interpolation: img.Interpolation.average);
  final bg = img.gaussianBlur(tiny, radius: 3);
  final out = img.Image(width: w, height: h, numChannels: 3);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      final p = src.getPixel(x, y);
      final b = bg.getPixelLinear(x * scale, y * scale);
      out.setPixelRgb(x, y, _norm(p.r, b.r), _norm(p.g, b.g), _norm(p.b, b.b));
    }
  }
  return out;
}

int _norm(num v, num bg) => (255 * v / (bg < 24 ? 24 : bg)).round().clamp(0, 255);

img.Image _adaptiveThreshold(img.Image src, {int shift = 0}) {
  final w = src.width, h = src.height;
  final gray = Uint8List(w * h);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      final p = src.getPixel(x, y);
      gray[y * w + x] = (0.299 * p.r + 0.587 * p.g + 0.114 * p.b).round();
    }
  }
  // integral image for a fast local mean
  final integral = Int64List((w + 1) * (h + 1));
  for (var y = 1; y <= h; y++) {
    var row = 0;
    for (var x = 1; x <= w; x++) {
      row += gray[(y - 1) * w + (x - 1)];
      integral[y * (w + 1) + x] = integral[(y - 1) * (w + 1) + x] + row;
    }
  }
  final r = ((w < h ? w : h) / 16).round().clamp(4, 64);
  final out = img.Image(width: w, height: h, numChannels: 3);
  for (var y = 0; y < h; y++) {
    final y0 = (y - r).clamp(0, h - 1), y1 = (y + r).clamp(0, h - 1);
    for (var x = 0; x < w; x++) {
      final x0 = (x - r).clamp(0, w - 1), x1 = (x + r).clamp(0, w - 1);
      final area = (x1 - x0 + 1) * (y1 - y0 + 1);
      final sum = integral[(y1 + 1) * (w + 1) + (x1 + 1)] - integral[y0 * (w + 1) + (x1 + 1)] - integral[(y1 + 1) * (w + 1) + x0] + integral[y0 * (w + 1) + x0];
      final mean = sum / area;
      final v = gray[y * w + x] < mean * 0.9 - shift ? 0 : 255;
      out.setPixelRgb(x, y, v, v, v);
    }
  }
  return out;
}
