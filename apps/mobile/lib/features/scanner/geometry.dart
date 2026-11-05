import 'dart:math' as math;

class Pt {
  const Pt(this.x, this.y);
  final double x;
  final double y;
  Pt operator -(Pt o) => Pt(x - o.x, y - o.y);
  double distanceTo(Pt o) => math.sqrt((x - o.x) * (x - o.x) + (y - o.y) * (y - o.y));
  Map<String, double> toJson() => {'x': x, 'y': y};
  static Pt fromJson(Map<String, dynamic> j) => Pt((j['x'] as num).toDouble(), (j['y'] as num).toDouble());
}

/// Four corners in the picture, in the order top-left, top-right, bottom-right, bottom-left. Coordinates are fractions of
/// the picture's width and height (0..1) so the same quad survives resizing.
class Quad {
  const Quad(this.tl, this.tr, this.br, this.bl);
  final Pt tl;
  final Pt tr;
  final Pt br;
  final Pt bl;

  /// The whole picture with a small inset: the fallback when no boundary is found.
  factory Quad.full({double inset = 0.0}) => Quad(Pt(inset, inset), Pt(1 - inset, inset), Pt(1 - inset, 1 - inset), Pt(inset, 1 - inset));

  List<Pt> get points => [tl, tr, br, bl];

  Quad withPoint(int i, Pt p) {
    final c = [...points];
    c[i] = p;
    return Quad(c[0], c[1], c[2], c[3]);
  }

  /// Shoelace area (fraction of the picture).
  double get area {
    final p = points;
    var s = 0.0;
    for (var i = 0; i < 4; i++) {
      final a = p[i], b = p[(i + 1) % 4];
      s += a.x * b.y - b.x * a.y;
    }
    return s.abs() / 2;
  }

  /// Convex, in the expected order, and not a sliver: the only shapes a perspective correction can use.
  bool get isUsable {
    final p = points;
    var sign = 0;
    for (var i = 0; i < 4; i++) {
      final a = p[i], b = p[(i + 1) % 4], c = p[(i + 2) % 4];
      final cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
      if (cross.abs() < 1e-9) return false;
      final s = cross > 0 ? 1 : -1;
      if (sign == 0) sign = s;
      if (s != sign) return false;
    }
    return area >= 0.05;
  }

  Quad rotated90() => Quad(bl, tl, tr, br); // used only for display-time rotation of the editor handles

  Map<String, dynamic> toJson() => {'tl': tl.toJson(), 'tr': tr.toJson(), 'br': br.toJson(), 'bl': bl.toJson()};
  static Quad fromJson(Map<String, dynamic> j) => Quad(Pt.fromJson(j['tl'] as Map<String, dynamic>), Pt.fromJson(j['tr'] as Map<String, dynamic>), Pt.fromJson(j['br'] as Map<String, dynamic>), Pt.fromJson(j['bl'] as Map<String, dynamic>));
}
