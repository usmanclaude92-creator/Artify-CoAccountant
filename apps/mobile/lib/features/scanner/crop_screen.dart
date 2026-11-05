import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'enhance.dart';
import 'geometry.dart';
import 'page_renderer.dart';
import 'scan_models.dart';
import 'scan_session.dart';

/// Adjust one page: drag the four corners (the app proposes them; "Whole picture" is the fallback), pick a look
/// (colour / grey / black and white), brightness, contrast and shadow removal, and see the straightened result.
class CropScreen extends StatefulWidget {
  const CropScreen({super.key, required this.session, required this.pageId});
  final ScanSession session;
  final String pageId;
  @override
  State<CropScreen> createState() => _CropScreenState();
}

class _CropScreenState extends State<CropScreen> {
  late ScanPage _page = widget.session.pages.firstWhere((p) => p.id == widget.pageId);
  Uint8List? _preview;
  bool _showResult = false;
  Timer? _debounce;
  int? _drag;
  Size? _imageSize;

  @override
  void initState() {
    super.initState();
    _loadSize();
  }

  Future<void> _loadSize() async {
    final bytes = await File(_page.rawPath).readAsBytes();
    final c = await decodeImageFromList(bytes);
    if (mounted) setState(() => _imageSize = Size(c.width.toDouble(), c.height.toDouble()));
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  void _commit() => widget.session.setQuad(_page.id, _page.quad);

  void _scheduleResult() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 250), () async {
      final bytes = await renderThumbnail(_page);
      if (mounted) setState(() => _preview = bytes);
    });
  }

  void _setAdjust(Adjust a) {
    setState(() => _page = _page.copyWith(adjust: a));
    widget.session.setAdjust(_page.id, a);
    _scheduleResult();
  }

  @override
  Widget build(BuildContext context) {
    final size = _imageSize;
    return Scaffold(
      appBar: AppBar(title: const Text('Adjust page'), actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Done'))]),
      body: SafeArea(
        child: Column(children: [
          Expanded(
            child: size == null
                ? const Center(child: CircularProgressIndicator())
                : LayoutBuilder(builder: (context, box) {
                    // fit the picture in the box, remember where it sits so handles can be placed on it
                    final scale = (box.maxWidth / size.width) < (box.maxHeight / size.height) ? box.maxWidth / size.width : box.maxHeight / size.height;
                    final w = size.width * scale, h = size.height * scale;
                    final dx = (box.maxWidth - w) / 2, dy = (box.maxHeight - h) / 2;
                    Offset toScreen(Pt p) => Offset(dx + p.x * w, dy + p.y * h);
                    Pt toNorm(Offset o) => Pt(((o.dx - dx) / w).clamp(0.0, 1.0), ((o.dy - dy) / h).clamp(0.0, 1.0));
                    return Stack(children: [
                      Positioned(left: dx, top: dy, width: w, height: h, child: _showResult && _preview != null ? Image.memory(_preview!, fit: BoxFit.contain, gaplessPlayback: true) : Image.file(File(_page.rawPath), fit: BoxFit.fill, cacheWidth: 1200)),
                      if (!_showResult)
                        Positioned.fill(
                          child: GestureDetector(
                            onPanStart: (d) {
                              var best = 0;
                              var bestD = double.infinity;
                              for (var i = 0; i < 4; i++) {
                                final dist = (toScreen(_page.quad.points[i]) - d.localPosition).distance;
                                if (dist < bestD) {
                                  bestD = dist;
                                  best = i;
                                }
                              }
                              if (bestD < 56) _drag = best;
                            },
                            onPanUpdate: (d) {
                              final i = _drag;
                              if (i == null) return;
                              final next = _page.quad.withPoint(i, toNorm(d.localPosition));
                              setState(() => _page = _page.copyWith(quad: next));
                            },
                            onPanEnd: (_) {
                              if (_drag != null) _commit();
                              _drag = null;
                            },
                            child: CustomPaint(painter: _QuadPainter(_page.quad, toScreen, valid: _page.quad.isUsable, color: Theme.of(context).colorScheme.primary), size: Size(box.maxWidth, box.maxHeight)),
                          ),
                        ),
                    ]);
                  }),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Row(children: [
              Expanded(child: Text(_page.autoDetected ? 'Page edges found automatically. Drag the corners to correct them.' : 'Drag the corners to the corners of the page.', style: Theme.of(context).textTheme.bodySmall)),
              TextButton(onPressed: () {
                widget.session.useFullPicture(_page.id);
                setState(() => _page = widget.session.pages.firstWhere((p) => p.id == _page.id));
              }, child: const Text('Whole picture')),
            ]),
          ),
          SegmentedButton<bool>(
            segments: const [ButtonSegment(value: false, label: Text('Edges')), ButtonSegment(value: true, label: Text('Result'))],
            selected: {_showResult},
            onSelectionChanged: (s) {
              setState(() => _showResult = s.first);
              if (s.first) _scheduleResult();
            },
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(children: [
              SegmentedButton<ScanMode>(
                segments: const [ButtonSegment(value: ScanMode.color, label: Text('Colour')), ButtonSegment(value: ScanMode.gray, label: Text('Grey')), ButtonSegment(value: ScanMode.blackWhite, label: Text('B & W'))],
                selected: {_page.adjust.mode},
                onSelectionChanged: (s) => _setAdjust(_page.adjust.copyWith(mode: s.first)),
              ),
              Row(children: [const SizedBox(width: 90, child: Text('Brightness')), Expanded(child: Slider(value: _page.adjust.brightness, min: -1, max: 1, onChanged: (v) => _setAdjust(_page.adjust.copyWith(brightness: v))))]),
              Row(children: [const SizedBox(width: 90, child: Text('Contrast')), Expanded(child: Slider(value: _page.adjust.contrast, min: -1, max: 1, onChanged: (v) => _setAdjust(_page.adjust.copyWith(contrast: v))))]),
              SwitchListTile(dense: true, contentPadding: EdgeInsets.zero, title: const Text('Reduce shadows'), value: _page.adjust.removeShadows, onChanged: (v) => _setAdjust(_page.adjust.copyWith(removeShadows: v))),
            ]),
          ),
        ]),
      ),
    );
  }
}

class _QuadPainter extends CustomPainter {
  _QuadPainter(this.quad, this.toScreen, {required this.valid, required this.color});
  final Quad quad;
  final Offset Function(Pt) toScreen;
  final bool valid;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final pts = [for (final p in quad.points) toScreen(p)];
    final path = Path()..addPolygon(pts, true);
    final c = valid ? color : Colors.red;
    canvas.drawPath(path, Paint()..color = c.withValues(alpha: 0.15));
    canvas.drawPath(path, Paint()..color = c..style = PaintingStyle.stroke..strokeWidth = 3);
    for (final p in pts) {
      canvas.drawCircle(p, 14, Paint()..color = Colors.white);
      canvas.drawCircle(p, 14, Paint()..color = c..style = PaintingStyle.stroke..strokeWidth = 3);
    }
  }

  @override
  bool shouldRepaint(_QuadPainter old) => old.quad != quad || old.valid != valid;
}
