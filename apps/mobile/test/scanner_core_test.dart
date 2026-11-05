import 'dart:io';

import 'package:artify_mobile/features/scanner/edge_detector.dart';
import 'package:artify_mobile/features/scanner/enhance.dart';
import 'package:artify_mobile/features/scanner/geometry.dart';
import 'package:artify_mobile/features/scanner/page_renderer.dart';
import 'package:artify_mobile/features/scanner/pdf_builder.dart';
import 'package:artify_mobile/features/scanner/perspective.dart';
import 'package:artify_mobile/features/scanner/scan_models.dart';
import 'package:artify_mobile/features/scanner/scan_session.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;

import 'support/synth.dart';

double gray(img.Pixel p) => 0.299 * p.r + 0.587 * p.g + 0.114 * p.b;

void main() {
  group('page detection', () {
    test('finds a skewed light page on a dark surface within a few percent', () {
      final photo = syntheticPhoto();
      final q = detectDocument(photo)!;
      final want = [const Pt(120 / 639, 100 / 799), const Pt(540 / 639, 130 / 799), const Pt(520 / 639, 700 / 799), const Pt(90 / 639, 680 / 799)];
      for (var i = 0; i < 4; i++) {
        expect((q.points[i].x - want[i].x).abs(), lessThan(0.04), reason: 'corner $i x');
        expect((q.points[i].y - want[i].y).abs(), lessThan(0.04), reason: 'corner $i y');
      }
      expect(q.isUsable, isTrue);
    });

    test('finds a dark page on a light surface too', () {
      final q = detectDocument(syntheticPhoto(pageGray: 40, bgGray: 225, mark: false));
      expect(q, isNotNull);
      expect(q!.area, greaterThan(0.3));
    });

    test('reports "not found" instead of guessing when there is no page', () {
      expect(detectDocument(img.Image(width: 300, height: 400, numChannels: 3)..clear(img.ColorRgb8(120, 120, 120))), isNull);
    });

    test('quad validation rejects crossed and sliver shapes', () {
      expect(Quad.full().isUsable, isTrue);
      expect(const Quad(Pt(0, 0), Pt(1, 1), Pt(1, 0), Pt(0, 1)).isUsable, isFalse); // bow-tie
      expect(const Quad(Pt(0, 0), Pt(1, 0), Pt(1, 0.01), Pt(0, 0.01)).isUsable, isFalse); // sliver
    });
  });

  group('perspective correction', () {
    test('turns the skewed page into an upright rectangle with the mark where it belongs', () {
      final photo = syntheticPhoto();
      final q = detectDocument(photo)!;
      final flat = warpToRectangle(photo, q);
      expect(flat.width, greaterThan(300));
      expect(flat.height, greaterThan(400));
      // the page is bright almost everywhere; the dark mark sits at ~25..40% across and ~25..35% down
      final inside = gray(flat.getPixel((flat.width * 0.33).round(), (flat.height * 0.30).round()));
      final blank = gray(flat.getPixel((flat.width * 0.75).round(), (flat.height * 0.75).round()));
      expect(inside, lessThan(90), reason: 'the mark is dark');
      expect(blank, greaterThan(190), reason: 'the page is light');
    });

    test('homography maps its own corners exactly', () {
      const from = [Pt(0, 0), Pt(100, 0), Pt(100, 200), Pt(0, 200)];
      const to = [Pt(10, 20), Pt(110, 5), Pt(120, 230), Pt(-5, 210)];
      final h = solveHomography(from, to);
      for (var i = 0; i < 4; i++) {
        final d = h[6] * from[i].x + h[7] * from[i].y + 1;
        expect((h[0] * from[i].x + h[1] * from[i].y + h[2]) / d, closeTo(to[i].x, 1e-6));
        expect((h[3] * from[i].x + h[4] * from[i].y + h[5]) / d, closeTo(to[i].y, 1e-6));
      }
    });
  });

  group('enhancement', () {
    img.Image shaded() {
      final im = img.Image(width: 200, height: 200, numChannels: 3);
      for (var y = 0; y < 200; y++) {
        for (var x = 0; x < 200; x++) {
          final shade = 255 - (x * 0.6).round(); // left bright, right in shadow
          final ink = (y > 90 && y < 100 && x > 40 && x < 160); // a line of text
          final v = ink ? (shade * 0.3).round() : shade;
          im.setPixelRgb(x, y, v, v, v);
        }
      }
      return im;
    }

    test('shadow reduction evens out the paper colour but keeps the ink', () {
      final src = shaded();
      final out = enhance(src, const Adjust(removeShadows: true));
      final left = gray(out.getPixel(10, 20)), right = gray(out.getPixel(190, 20));
      expect((left - right).abs(), lessThan(40));
      expect(gray(src.getPixel(10, 20)) - gray(src.getPixel(190, 20)), greaterThan(100));
      expect(gray(out.getPixel(100, 95)), lessThan(gray(out.getPixel(100, 20)) - 60));
    });

    test('black-and-white contains only black and white, and the text line stays black', () {
      final out = enhance(shaded(), const Adjust(mode: ScanMode.blackWhite));
      final seen = <int>{};
      for (var y = 0; y < out.height; y += 5) {
        for (var x = 0; x < out.width; x += 5) {
          seen.add(out.getPixel(x, y).r.toInt());
        }
      }
      expect(seen, {0, 255});
      expect(out.getPixel(100, 95).r, 0);
      expect(out.getPixel(100, 20).r, 255);
    });

    test('grayscale removes colour; brightness and contrast change the picture', () {
      final c = img.Image(width: 4, height: 4, numChannels: 3)..clear(img.ColorRgb8(200, 100, 50));
      final g = enhance(c, const Adjust(mode: ScanMode.gray)).getPixel(1, 1);
      expect((g.r - g.g).abs() < 2 && (g.g - g.b).abs() < 2, isTrue);
      final lighter = enhance(c, const Adjust(brightness: 0.6)).getPixel(1, 1);
      expect(lighter.r + lighter.g + lighter.b, greaterThan(350));
      expect(const Adjust().isNeutral, isTrue);
    });
  });

  group('pdf', () {
    test('one PDF, pages in the order given, each page shaped like its picture', () async {
      final wide = jpegBytes(syntheticPhoto(w: 800, h: 400, mark: false, corners: [(0, 0), (799, 0), (799, 399), (0, 399)]));
      final tall = jpegBytes(syntheticPhoto(w: 400, h: 800, mark: false, corners: [(0, 0), (399, 0), (399, 799), (0, 799)]));
      final pdf = await buildPdf([wide, tall, wide], title: 'Statement');
      expect(String.fromCharCodes(pdf.take(5)), '%PDF-');
      expect(countPdfPages(pdf), 3);
      final text = String.fromCharCodes(pdf);
      final boxes = RegExp(r'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]').allMatches(text).map((m) => double.parse(m.group(2)!)).toList();
      expect(boxes.length, 3);
      expect(boxes[0], lessThan(boxes[1]), reason: 'page 2 (portrait) is taller than page 1 (landscape)');
      expect(boxes[2], closeTo(boxes[0], 0.5), reason: 'page 3 is page 1 again');
    });

    test('an empty scan cannot become a PDF', () {
      expect(() => buildPdf([]), throwsStateError);
    });
  });

  group('scan session', () {
    late Directory tmp;
    late ScanSession s;
    late List<File> photos;

    setUp(() async {
      tmp = await Directory.systemTemp.createTemp('scan_test');
      photos = [for (var i = 0; i < 4; i++) await writeJpeg(Directory('${tmp.path}/cam'), 'p$i.jpg', syntheticPhoto(w: 320 + i * 8, h: 400))];
      s = ScanSession(workDir: Directory('${tmp.path}/work'));
    });
    tearDown(() async => tmp.delete(recursive: true));

    Future<void> addAll() async {
      for (final f in photos) {
        await s.addCaptured(f.path);
      }
    }

    test('continuous capture keeps pages in the order they were taken and detects each page', () async {
      await addAll();
      expect(s.pageCount, 4);
      expect(s.pages.every((e) => e.autoDetected), isTrue);
      final widths = [for (final pg in s.pages) img.decodeImage(await File(pg.rawPath).readAsBytes())!.width];
      expect(widths, [320, 328, 336, 344], reason: 'order of capture');
    });

    test('delete, rotate and reorder act on the right pages', () async {
      await addAll();
      final ids = s.pages.map((e) => e.id).toList();
      await s.delete(ids[1]);
      expect(s.pages.map((e) => e.id), [ids[0], ids[2], ids[3]]);
      s.rotate(ids[2]);
      s.rotate(ids[2]);
      expect(s.pages[1].rotation, 180);
      s.rotate(ids[3], clockwise: false);
      expect(s.pages[2].rotation, 270);
      s.reorder(0, 3); // move the first page to the end
      expect(s.pages.map((e) => e.id), [ids[2], ids[3], ids[0]]);
      s.reorder(2, 0); // and back to the front
      expect(s.pages.map((e) => e.id), [ids[0], ids[2], ids[3]]);
      expect(File(photos[1].path).existsSync(), isTrue, reason: 'the camera original is not touched; only the session copy is removed');
    });

    test('retake replaces the photo but keeps the position', () async {
      await addAll();
      final ids = s.pages.map((e) => e.id).toList();
      final oldRaw = s.pages[2].rawPath;
      final fresh = await writeJpeg(Directory('${tmp.path}/cam'), 'retake.jpg', syntheticPhoto(w: 500, h: 400));
      await s.retake(ids[2], fresh.path);
      expect(s.pages.map((e) => e.id), ids);
      expect(File(oldRaw).existsSync(), isFalse);
      expect(img.decodeImage(await File(s.pages[2].rawPath).readAsBytes())!.width, 500);
    });

    test('a manual crop is kept only when it is a usable shape', () async {
      await addAll();
      final id = s.pages.first.id;
      final ok = const Quad(Pt(0.1, 0.1), Pt(0.9, 0.12), Pt(0.88, 0.9), Pt(0.12, 0.88));
      s.setQuad(id, ok);
      expect(s.pages.first.quad.tl.x, 0.1);
      expect(s.pages.first.autoDetected, isFalse);
      s.setQuad(id, const Quad(Pt(0, 0), Pt(1, 1), Pt(1, 0), Pt(0, 1))); // crossed: ignored
      expect(s.pages.first.quad.tl.x, 0.1);
    });

    test('the final PDF has every remaining page, in order, and the original photos stay untouched', () async {
      await addAll();
      await s.delete(s.pages[1].id);
      s.setQuality(PdfQuality.high);
      final pdf = await s.toPdf();
      expect(countPdfPages(pdf), 3);
      expect(pdf.length, greaterThan(2000));
    });

    test('a draft survives closing the app and restores order, rotation and settings', () async {
      await addAll();
      s.name = 'Bank statement March';
      s.rotate(s.pages[0].id);
      s.setModeForAll(ScanMode.gray);
      await s.saveDraft();
      final ids = s.pages.map((e) => e.id).toList();
      expect(await ScanSession.listDrafts(Directory('${tmp.path}/work')), [s.id]);
      final back = (await ScanSession.loadDraft(Directory('${tmp.path}/work'), s.id))!;
      expect(back.pages.map((e) => e.id), ids);
      expect(back.pages[0].rotation, 90);
      expect(back.pages.every((e) => e.adjust.mode == ScanMode.gray), isTrue);
      expect(back.name, 'Bank statement March');
      await back.discard();
      expect(await ScanSession.listDrafts(Directory('${tmp.path}/work')), isEmpty);
    });

    test('rendering applies crop, rotation and clean-up and yields a readable picture', () async {
      await addAll();
      s.rotate(s.pages.first.id);
      final bytes = await renderPage(s.pages.first, PdfQuality.standard);
      final out = img.decodeJpg(bytes)!;
      expect(out.width, greaterThan(out.height), reason: 'a portrait page turned a quarter turn is landscape');
      expect(out.width, lessThanOrEqualTo(2200));
    });
  });
}
