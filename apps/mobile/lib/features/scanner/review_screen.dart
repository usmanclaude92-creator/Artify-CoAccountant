import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

import '../../app_services.dart';
import '../../core/widgets/common.dart';
import '../upload/upload_form_screen.dart';
import '../upload/upload_models.dart';
import 'crop_screen.dart';
import 'enhance.dart';
import 'page_renderer.dart';
import 'pdf_preview_screen.dart';
import 'scan_models.dart';
import 'scan_session.dart';

/// The pages of one scan, in order: reorder, rotate, retake, adjust, delete, add more, preview the PDF, then send it.
class ReviewScreen extends StatefulWidget {
  const ReviewScreen({super.key, required this.services, required this.session});
  final AppServices services;
  final ScanSession session;
  @override
  State<ReviewScreen> createState() => _ReviewScreenState();
}

class _ReviewScreenState extends State<ReviewScreen> {
  final Map<String, Uint8List> _thumbs = {};
  final Map<String, String> _thumbKey = {};
  bool _busy = false;

  ScanSession get s => widget.session;

  Future<Uint8List?> _thumb(ScanPage pg) async {
    final key = '${pg.rawPath}|${pg.rotation}|${pg.quad.toJson()}|${pg.adjust.toJson()}';
    if (_thumbKey[pg.id] == key && _thumbs[pg.id] != null) return _thumbs[pg.id];
    final bytes = await renderThumbnail(pg);
    _thumbs[pg.id] = bytes;
    _thumbKey[pg.id] = key;
    return bytes;
  }

  Future<void> _retake(ScanPage pg) async {
    final picked = await widget.services.imports.pickPhotos();
    if (picked.isEmpty) return;
    await s.retake(pg.id, picked.first.path);
  }

  Future<void> _send() async {
    setState(() => _busy = true);
    try {
      final pdf = await s.toPdf();
      final stamp = DateTime.now();
      String two(int v) => v.toString().padLeft(2, '0');
      final base = s.name.trim().isEmpty ? 'Scan ${stamp.year}-${two(stamp.month)}-${two(stamp.day)} ${two(stamp.hour)}${two(stamp.minute)}' : s.name.trim();
      final out = File(p.join(widget.services.scanDir.path, s.id, '$base.pdf'));
      await out.parent.create(recursive: true);
      await out.writeAsBytes(pdf, flush: true);
      if (!mounted) return;
      final sent = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => UploadFormScreen(services: widget.services, filePath: out.path, fileName: p.basename(out.path), source: UploadSource.scan, pages: s.pageCount, suggestedName: base)));
      if (sent == true) {
        await s.discard(); // the PDF was copied into the upload queue
        if (mounted) Navigator.of(context).popUntil((r) => r.isFirst);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _discard() async {
    final ok = await showDialog<bool>(context: context, builder: (_) => AlertDialog(title: const Text('Discard this scan?'), content: Text('${s.pageCount} page${s.pageCount == 1 ? '' : 's'} will be deleted from this phone.'), actions: [TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep')), FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Discard'))]));
    if (ok == true) {
      await s.discard();
      if (mounted) Navigator.of(context).popUntil((r) => r.isFirst);
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: s,
        builder: (context, _) => Scaffold(
          appBar: AppBar(title: Text('${s.pageCount} page${s.pageCount == 1 ? '' : 's'}'), actions: [
            IconButton(tooltip: 'Save draft', icon: const Icon(Icons.save_outlined), onPressed: () async {
              await s.saveDraft();
              if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Draft saved on this phone.')));
            }),
            IconButton(tooltip: 'Discard scan', icon: const Icon(Icons.delete_outline), onPressed: _discard),
          ]),
          body: SafeArea(
            child: s.isEmpty
                ? const StateMessage(icon: Icons.note_add_outlined, title: 'No pages', body: 'Go back and scan a page.')
                : Column(children: [
                    Padding(
                      padding: const EdgeInsets.all(12),
                      child: Row(children: [
                        Expanded(child: SegmentedButton<ScanMode>(segments: const [ButtonSegment(value: ScanMode.color, label: Text('Colour')), ButtonSegment(value: ScanMode.gray, label: Text('Grey')), ButtonSegment(value: ScanMode.blackWhite, label: Text('B & W'))], selected: {s.pages.first.adjust.mode}, onSelectionChanged: (v) => s.setModeForAll(v.first))),
                        const SizedBox(width: 8),
                        SegmentedButton<PdfQuality>(segments: const [ButtonSegment(value: PdfQuality.standard, label: Text('Std')), ButtonSegment(value: PdfQuality.high, label: Text('High'))], selected: {s.quality}, onSelectionChanged: (v) => s.setQuality(v.first)),
                      ]),
                    ),
                    Expanded(
                      child: ReorderableListView.builder(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        itemCount: s.pageCount,
                        onReorderItem: (o, n) => s.reorder(o, n > o ? n + 1 : n),
                        itemBuilder: (context, i) {
                          final pg = s.pages[i];
                          return Card(
                            key: ValueKey(pg.id),
                            margin: const EdgeInsets.only(bottom: 8),
                            child: Padding(
                              padding: const EdgeInsets.all(8),
                              child: Row(children: [
                                SizedBox(width: 72, height: 96, child: FutureBuilder<Uint8List?>(future: _thumb(pg), builder: (_, snap) => snap.data == null ? const Center(child: CircularProgressIndicator(strokeWidth: 2)) : Image.memory(snap.data!, fit: BoxFit.contain, gaplessPlayback: true))),
                                const SizedBox(width: 12),
                                Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text('Page ${i + 1}', style: Theme.of(context).textTheme.titleSmall), Text(pg.autoDetected ? 'Edges found automatically' : 'Edges set by hand', style: Theme.of(context).textTheme.bodySmall)])),
                                PopupMenuButton<String>(
                                  tooltip: 'Page ${i + 1} actions',
                                  onSelected: (a) async {
                                    switch (a) {
                                      case 'rotate':
                                        s.rotate(pg.id);
                                      case 'edit':
                                        await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => CropScreen(session: s, pageId: pg.id)));
                                      case 'retake':
                                        await _retake(pg);
                                      case 'delete':
                                        await s.delete(pg.id);
                                    }
                                  },
                                  itemBuilder: (_) => const [
                                    PopupMenuItem(value: 'rotate', child: Text('Rotate')),
                                    PopupMenuItem(value: 'edit', child: Text('Adjust edges and look')),
                                    PopupMenuItem(value: 'retake', child: Text('Replace with another photo')),
                                    PopupMenuItem(value: 'delete', child: Text('Delete page')),
                                  ],
                                ),
                                const Icon(Icons.drag_handle),
                              ]),
                            ),
                          );
                        },
                      ),
                    ),
                    Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(children: [
                        Row(children: [
                          Expanded(child: OutlinedButton.icon(icon: const Icon(Icons.add_a_photo_outlined), label: const Text('Add page'), onPressed: () => Navigator.of(context).pop())),
                          const SizedBox(width: 8),
                          Expanded(child: OutlinedButton.icon(icon: const Icon(Icons.picture_as_pdf_outlined), label: const Text('Preview PDF'), onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => PdfPreviewScreen(session: s))))),
                        ]),
                        const SizedBox(height: 8),
                        SizedBox(width: double.infinity, child: FilledButton.icon(icon: _busy ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.arrow_forward), label: const Text('Continue'), onPressed: _busy ? null : _send)),
                      ]),
                    ),
                  ]),
          ),
        ),
      );
}
