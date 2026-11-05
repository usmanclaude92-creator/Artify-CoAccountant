import 'dart:io';
import 'dart:typed_data';

import 'package:artify_mobile/features/scanner/geometry.dart';
import 'package:image/image.dart' as img;

/// Synthetic "photos": a light page (with a dark mark at a known place) on a dark surface. No real documents anywhere.
img.Image syntheticPhoto({int w = 640, int h = 800, List<(int, int)>? corners, int pageGray = 235, int bgGray = 40, bool mark = true}) {
  final im = img.Image(width: w, height: h, numChannels: 3);
  img.fill(im, color: img.ColorRgb8(bgGray, bgGray, bgGray));
  final c = corners ?? [(120, 100), (540, 130), (520, 700), (90, 680)]; // tl, tr, br, bl (a slightly rotated, skewed page)
  img.fillPolygon(im, vertices: [for (final p in c) img.Point(p.$1, p.$2)], color: img.ColorRgb8(pageGray, pageGray, pageGray));
  if (mark) {
    // a dark block at 25..40% across and 25..35% down the page, in page coordinates
    Pt lerp(Pt a, Pt b, double t) => Pt(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);
    final tl = Pt(c[0].$1.toDouble(), c[0].$2.toDouble()), tr = Pt(c[1].$1.toDouble(), c[1].$2.toDouble()), br = Pt(c[2].$1.toDouble(), c[2].$2.toDouble()), bl = Pt(c[3].$1.toDouble(), c[3].$2.toDouble());
    Pt at(double u, double v) {
      final top = lerp(tl, tr, u), bottom = lerp(bl, br, u);
      return lerp(top, bottom, v);
    }
    final a = at(0.25, 0.25), b = at(0.40, 0.25), d = at(0.40, 0.35), e = at(0.25, 0.35);
    img.fillPolygon(im, vertices: [img.Point(a.x.round(), a.y.round()), img.Point(b.x.round(), b.y.round()), img.Point(d.x.round(), d.y.round()), img.Point(e.x.round(), e.y.round())], color: img.ColorRgb8(10, 10, 10));
  }
  return im;
}

Uint8List jpegBytes(img.Image im) => Uint8List.fromList(img.encodeJpg(im, quality: 92));

Future<File> writeJpeg(Directory dir, String name, img.Image im) async {
  await dir.create(recursive: true);
  final f = File('${dir.path}/$name');
  await f.writeAsBytes(jpegBytes(im));
  return f;
}
