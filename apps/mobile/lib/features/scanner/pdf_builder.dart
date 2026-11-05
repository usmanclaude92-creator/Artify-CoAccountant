import 'dart:typed_data';

import 'package:image/image.dart' as img;
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;

/// One PDF from the rendered pages, in order. Each PDF page takes the shape of its picture (nothing is stretched or
/// cropped), at A4 width, with no margin, so a scanned invoice fills the page and stays legible.
Future<Uint8List> buildPdf(List<Uint8List> jpegPages, {String title = 'Scan'}) async {
  if (jpegPages.isEmpty) throw StateError('A PDF needs at least one page.');
  final doc = pw.Document(title: title, creator: 'Artify Co-Accountant');
  for (final bytes in jpegPages) {
    final probe = img.decodeJpg(bytes);
    final ratio = probe == null || probe.width == 0 ? 1.414 : probe.height / probe.width;
    final format = PdfPageFormat(595.28, 595.28 * ratio, marginAll: 0);
    final image = pw.MemoryImage(bytes);
    doc.addPage(pw.Page(pageFormat: format, margin: pw.EdgeInsets.zero, build: (_) => pw.Image(image, fit: pw.BoxFit.fill)));
  }
  return doc.save();
}

/// Counts the pages of a PDF written by [buildPdf] (used by the app to show "12 pages" and by tests).
int countPdfPages(Uint8List pdf) {
  final text = String.fromCharCodes(pdf);
  final m = RegExp(r'/Type\s*/Pages[\s\S]*?/Count\s+(\d+)').firstMatch(text);
  return m == null ? 0 : int.parse(m.group(1)!);
}
