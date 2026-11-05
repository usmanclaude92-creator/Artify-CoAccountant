import 'dart:typed_data';

import 'package:image/image.dart' as img;

import 'geometry.dart';

/// Finds the page in a photo without any native library: grey-scale, blur, Otsu threshold, the largest connected
/// bright (or dark) region, and its four extreme corners. It works well for a light page on a darker surface and is
/// honest when it cannot: it returns null and the app offers the whole picture plus manual corner handles.
///
/// Limits (documented, not hidden): a white page on a white desk, a page that fills the frame, or heavy clutter around
/// the page may fall back to manual adjustment. No machine-learning model is involved.
Quad? detectDocument(img.Image source) {
  const maxSide = 320;
  final scale = maxSide / (source.width > source.height ? source.width : source.height);
  final small = scale < 1 ? img.copyResize(source, width: (source.width * scale).round().clamp(8, maxSide), height: (source.height * scale).round().clamp(8, maxSide), interpolation: img.Interpolation.average) : source.clone();
  final w = small.width, h = small.height;
  final gray = Uint8List(w * h);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      final p = small.getPixel(x, y);
      gray[y * w + x] = (0.299 * p.r + 0.587 * p.g + 0.114 * p.b).round().clamp(0, 255);
    }
  }
  final blurred = _boxBlur(gray, w, h, 2);
  final t = _otsu(blurred);
  for (final lightIsPage in const [true, false]) {
    final mask = Uint8List(w * h);
    var count = 0;
    for (var i = 0; i < mask.length; i++) {
      final on = lightIsPage ? blurred[i] > t : blurred[i] <= t;
      if (on) {
        mask[i] = 1;
        count++;
      }
    }
    final frac = count / mask.length;
    if (frac < 0.12 || frac > 0.96) continue;
    final comp = _largestComponent(mask, w, h);
    if (comp == null) continue;
    final area = comp.size / mask.length;
    if (area < 0.12 || area > 0.97) continue;
    final q = Quad(Pt(comp.tl.$1 / (w - 1), comp.tl.$2 / (h - 1)), Pt(comp.tr.$1 / (w - 1), comp.tr.$2 / (h - 1)), Pt(comp.br.$1 / (w - 1), comp.br.$2 / (h - 1)), Pt(comp.bl.$1 / (w - 1), comp.bl.$2 / (h - 1)));
    // the quad must explain most of the region; otherwise it is clutter, not a page
    if (q.isUsable && comp.size / (q.area * w * h) > 0.75 && comp.size / (q.area * w * h) < 1.25) return q;
  }
  return null;
}

class _Component {
  _Component(this.size, this.tl, this.tr, this.br, this.bl);
  final int size;
  final (int, int) tl, tr, br, bl;
}

_Component? _largestComponent(Uint8List mask, int w, int h) {
  final seen = Uint8List(w * h);
  final stack = Int32List(w * h);
  _Component? best;
  for (var start = 0; start < mask.length; start++) {
    if (mask[start] == 0 || seen[start] == 1) continue;
    var sp = 0;
    stack[sp++] = start;
    seen[start] = 1;
    var size = 0;
    var minSum = 1 << 30, maxSum = -1, minDiff = 1 << 30, maxDiff = -(1 << 30);
    (int, int) tl = (0, 0), br = (0, 0), tr = (0, 0), bl = (0, 0);
    while (sp > 0) {
      final i = stack[--sp];
      final x = i % w, y = i ~/ w;
      size++;
      final sum = x + y, diff = x - y;
      if (sum < minSum) { minSum = sum; tl = (x, y); }
      if (sum > maxSum) { maxSum = sum; br = (x, y); }
      if (diff > maxDiff) { maxDiff = diff; tr = (x, y); }
      if (diff < minDiff) { minDiff = diff; bl = (x, y); }
      if (x > 0 && mask[i - 1] == 1 && seen[i - 1] == 0) { seen[i - 1] = 1; stack[sp++] = i - 1; }
      if (x < w - 1 && mask[i + 1] == 1 && seen[i + 1] == 0) { seen[i + 1] = 1; stack[sp++] = i + 1; }
      if (y > 0 && mask[i - w] == 1 && seen[i - w] == 0) { seen[i - w] = 1; stack[sp++] = i - w; }
      if (y < h - 1 && mask[i + w] == 1 && seen[i + w] == 0) { seen[i + w] = 1; stack[sp++] = i + w; }
    }
    if (best == null || size > best.size) best = _Component(size, tl, tr, br, bl);
  }
  return best;
}

Uint8List _boxBlur(Uint8List src, int w, int h, int r) {
  final tmp = Uint8List(w * h), out = Uint8List(w * h);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      var s = 0, n = 0;
      for (var k = -r; k <= r; k++) {
        final xx = x + k;
        if (xx >= 0 && xx < w) { s += src[y * w + xx]; n++; }
      }
      tmp[y * w + x] = s ~/ n;
    }
  }
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      var s = 0, n = 0;
      for (var k = -r; k <= r; k++) {
        final yy = y + k;
        if (yy >= 0 && yy < h) { s += tmp[yy * w + x]; n++; }
      }
      out[y * w + x] = s ~/ n;
    }
  }
  return out;
}

int _otsu(Uint8List g) {
  final hist = List<int>.filled(256, 0);
  for (final v in g) { hist[v]++; }
  final total = g.length;
  var sum = 0.0;
  for (var i = 0; i < 256; i++) { sum += i * hist[i]; }
  var sumB = 0.0, wB = 0, best = 0.0, thr = 127;
  for (var t = 0; t < 256; t++) {
    wB += hist[t];
    if (wB == 0) continue;
    final wF = total - wB;
    if (wF == 0) break;
    sumB += t * hist[t];
    final mB = sumB / wB, mF = (sum - sumB) / wF;
    final between = wB.toDouble() * wF * (mB - mF) * (mB - mF);
    if (between > best) { best = between; thr = t; }
  }
  return thr;
}
