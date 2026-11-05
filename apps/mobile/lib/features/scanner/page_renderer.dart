import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:image/image.dart' as img;

import 'edge_detector.dart';
import 'enhance.dart';
import 'geometry.dart';
import 'perspective.dart';
import 'scan_models.dart';

class RenderRequest {
  const RenderRequest(this.bytes, this.quad, this.rotation, this.adjust, this.longEdge, this.jpegQuality);
  final Uint8List bytes;
  final Quad quad;
  final int rotation;
  final Adjust adjust;
  final int longEdge;
  final int jpegQuality;
}

/// Decode (honouring the camera's orientation), straighten, clean up, rotate, encode. Runs in a background isolate.
Uint8List renderPageSync(RenderRequest r) {
  final decoded = img.decodeImage(r.bytes);
  if (decoded == null) throw const FormatException('The picture could not be read.');
  var im = img.bakeOrientation(decoded);
  im = warpToRectangle(im, r.quad, maxLongEdge: r.longEdge);
  im = enhance(im, r.adjust);
  if (r.rotation % 360 != 0) im = img.copyRotate(im, angle: r.rotation % 360);
  return Uint8List.fromList(img.encodeJpg(im, quality: r.jpegQuality));
}

Future<Uint8List> renderPage(ScanPage page, PdfQuality quality) async {
  final bytes = await File(page.rawPath).readAsBytes();
  return compute(renderPageSync, RenderRequest(bytes, page.quad, page.rotation, page.adjust, quality.longEdge, quality.jpegQuality));
}

/// Small picture for the page gallery.
Uint8List thumbnailSync(RenderRequest r) {
  final decoded = img.decodeImage(r.bytes);
  if (decoded == null) throw const FormatException('The picture could not be read.');
  var im = img.bakeOrientation(decoded);
  im = img.copyResize(im, width: im.width >= im.height ? 360 : null, height: im.height > im.width ? 360 : null, interpolation: img.Interpolation.average);
  im = warpToRectangle(im, r.quad, maxLongEdge: 360);
  im = enhance(im, r.adjust);
  if (r.rotation % 360 != 0) im = img.copyRotate(im, angle: r.rotation % 360);
  return Uint8List.fromList(img.encodeJpg(im, quality: 70));
}

Future<Uint8List> renderThumbnail(ScanPage page) async =>
    compute(thumbnailSync, RenderRequest(await File(page.rawPath).readAsBytes(), page.quad, page.rotation, page.adjust, 360, 70));

/// Finds the page outline in a stored photo; null when none is found (the caller then offers the full picture).
Quad? detectInBytesSync(Uint8List bytes) {
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return null;
  return detectDocument(img.bakeOrientation(decoded));
}

Future<Quad?> detectInFile(String path) async => compute(detectInBytesSync, await File(path).readAsBytes());
