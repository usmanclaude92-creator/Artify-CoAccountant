import 'dart:math' as math;

import 'package:image/image.dart' as img;

import 'geometry.dart';

/// Straightens the page: the quadrilateral [q] (fractions of the picture) becomes a rectangle whose size follows the
/// page's own edge lengths. Pure Dart, bilinear sampling, no native code.
img.Image warpToRectangle(img.Image src, Quad q, {int maxLongEdge = 2400}) {
  final sw = src.width.toDouble(), sh = src.height.toDouble();
  final p = [for (final c in q.points) Pt(c.x * (sw - 1), c.y * (sh - 1))];
  final top = p[0].distanceTo(p[1]), bottom = p[3].distanceTo(p[2]);
  final left = p[0].distanceTo(p[3]), right = p[1].distanceTo(p[2]);
  var w = math.max(top, bottom), h = math.max(left, right);
  final k = math.max(w, h) > maxLongEdge ? maxLongEdge / math.max(w, h) : 1.0;
  w *= k;
  h *= k;
  final dw = math.max(2, w.round()), dh = math.max(2, h.round());
  // homography that maps destination pixels to source pixels
  final dst = [const Pt(0, 0), Pt(dw - 1.0, 0), Pt(dw - 1.0, dh - 1.0), Pt(0, dh - 1.0)];
  final H = solveHomography(dst, p);
  final out = img.Image(width: dw, height: dh, numChannels: 3);
  for (var y = 0; y < dh; y++) {
    for (var x = 0; x < dw; x++) {
      final den = H[6] * x + H[7] * y + 1.0;
      final sx = (H[0] * x + H[1] * y + H[2]) / den;
      final sy = (H[3] * x + H[4] * y + H[5]) / den;
      final px = src.getPixelLinear(sx.clamp(0, sw - 1), sy.clamp(0, sh - 1));
      out.setPixelRgb(x, y, px.r, px.g, px.b);
    }
  }
  return out;
}

/// Eight homography coefficients (h0..h7, h8 = 1) mapping each `from[i]` to `to[i]`; Gaussian elimination with pivoting.
List<double> solveHomography(List<Pt> from, List<Pt> to) {
  final a = List.generate(8, (_) => List<double>.filled(9, 0));
  for (var i = 0; i < 4; i++) {
    final x = from[i].x, y = from[i].y, u = to[i].x, v = to[i].y;
    a[2 * i] = [x, y, 1, 0, 0, 0, -u * x, -u * y, u];
    a[2 * i + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y, v];
  }
  for (var c = 0; c < 8; c++) {
    var piv = c;
    for (var r = c + 1; r < 8; r++) {
      if (a[r][c].abs() > a[piv][c].abs()) piv = r;
    }
    final t = a[c];
    a[c] = a[piv];
    a[piv] = t;
    final d = a[c][c];
    if (d.abs() < 1e-12) return [1, 0, 0, 0, 1, 0, 0, 0];
    for (var k = c; k < 9; k++) {
      a[c][k] /= d;
    }
    for (var r = 0; r < 8; r++) {
      if (r == c) continue;
      final f = a[r][c];
      if (f == 0) continue;
      for (var k = c; k < 9; k++) {
        a[r][k] -= f * a[c][k];
      }
    }
  }
  return [for (var i = 0; i < 8; i++) a[i][8]];
}
