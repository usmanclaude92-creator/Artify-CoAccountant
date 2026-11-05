import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../core/widgets/common.dart';
import 'pdf_builder.dart';
import 'scan_session.dart';

/// Shows the pages exactly as they will be embedded in the PDF, in order, with the size of the finished file.
class PdfPreviewScreen extends StatefulWidget {
  const PdfPreviewScreen({super.key, required this.session});
  final ScanSession session;
  @override
  State<PdfPreviewScreen> createState() => _PdfPreviewScreenState();
}

class _PdfPreviewScreenState extends State<PdfPreviewScreen> {
  late final Future<(List<Uint8List>, int)> _built = () async {
    final pages = await widget.session.renderAll();
    final pdf = await buildPdf(pages);
    return (pages, pdf.length);
  }();

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('PDF preview')),
        body: FutureBuilder<(List<Uint8List>, int)>(
          future: _built,
          builder: (context, snap) {
            if (snap.hasError) return const StateMessage(icon: Icons.error_outline, title: 'The preview could not be made');
            if (!snap.hasData) return const Center(child: CircularProgressIndicator());
            final (pages, size) = snap.data!;
            return ListView(padding: const EdgeInsets.all(12), children: [
              Text('${pages.length} page${pages.length == 1 ? '' : 's'} · ${fmtBytes(size)} · one PDF file', style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 8),
              for (var i = 0; i < pages.length; i++)
                Padding(padding: const EdgeInsets.only(bottom: 12), child: Card(clipBehavior: Clip.antiAlias, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Padding(padding: const EdgeInsets.all(8), child: Text('Page ${i + 1}')), InteractiveViewer(child: Image.memory(pages[i], fit: BoxFit.fitWidth, width: double.infinity))]))),
            ]);
          },
        ),
      );
}
