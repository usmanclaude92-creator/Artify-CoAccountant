import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/widgets/common.dart';
import 'crop_screen.dart';
import 'review_screen.dart';
import 'scan_session.dart';

/// Live camera for continuous scanning: every shot becomes the next page of ONE document. When the camera cannot be used
/// (permission refused, no camera) the person is told why and can still pick photos from the gallery.
class ScannerScreen extends StatefulWidget {
  const ScannerScreen({super.key, required this.services, required this.session});
  final AppServices services;
  final ScanSession session;
  @override
  State<ScannerScreen> createState() => _ScannerScreenState();
}

class _ScannerScreenState extends State<ScannerScreen> with WidgetsBindingObserver {
  CameraController? _cam;
  List<CameraDescription> _cams = const [];
  int _index = 0;
  FlashMode _flash = FlashMode.off;
  String? _problem;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _start();
  }

  Future<void> _start() async {
    try {
      _cams = await availableCameras();
      if (_cams.isEmpty) return setState(() => _problem = 'This phone has no usable camera.');
      await _open(_cams.indexWhere((c) => c.lensDirection == CameraLensDirection.back).clamp(0, _cams.length - 1));
    } on CameraException catch (e) {
      setState(() => _problem = e.code == 'CameraAccessDenied' || e.code == 'CameraAccessDeniedWithoutPrompt' || e.code == 'CameraAccessRestricted'
          ? 'Camera access is turned off for Artify. Allow it in your phone settings, or choose photos from the gallery instead.'
          : 'The camera could not be started (${e.code}).');
    } on Object {
      setState(() => _problem = 'The camera could not be started.');
    }
  }

  Future<void> _open(int i) async {
    final old = _cam;
    final c = CameraController(_cams[i], ResolutionPreset.veryHigh, enableAudio: false);
    await c.initialize();
    await c.setFlashMode(_flash).catchError((_) {});
    _index = i;
    if (!mounted) return c.dispose();
    setState(() {
      _cam = c;
      _problem = null;
    });
    await old?.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final c = _cam;
    if (c == null || !c.value.isInitialized) return;
    if (state == AppLifecycleState.inactive) {
      c.dispose();
      _cam = null;
    } else if (state == AppLifecycleState.resumed && _cams.isNotEmpty) {
      _open(_index);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _cam?.dispose();
    super.dispose();
  }

  Future<void> _shoot() async {
    final c = _cam;
    if (c == null || _busy || !c.value.isInitialized) return;
    setState(() => _busy = true);
    try {
      final shot = await c.takePicture();
      final page = await widget.session.addCaptured(shot.path);
      if (!mounted) return;
      await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => CropScreen(session: widget.session, pageId: page.id)));
    } on CameraException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Could not take the picture (${e.code}).')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _gallery() async {
    final picked = await widget.services.imports.pickPhotos();
    for (final f in picked) {
      await widget.session.addCaptured(f.path);
    }
    if (picked.isNotEmpty && mounted) _review();
  }

  void _review() => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => ReviewScreen(services: widget.services, session: widget.session)));

  Future<void> _cycleFlash() async {
    final c = _cam;
    if (c == null) return;
    const order = [FlashMode.off, FlashMode.auto, FlashMode.always, FlashMode.torch];
    final next = order[(order.indexOf(_flash) + 1) % order.length];
    try {
      await c.setFlashMode(next);
      setState(() => _flash = next);
    } on CameraException {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('This camera has no flash.')));
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.session,
        builder: (context, _) {
          final c = _cam;
          return Scaffold(
            backgroundColor: Colors.black,
            appBar: AppBar(
              backgroundColor: Colors.black,
              foregroundColor: Colors.white,
              title: Text(widget.session.pageCount == 0 ? 'Scan' : 'Page ${widget.session.pageCount + 1}'),
              actions: [
                IconButton(tooltip: 'Flash: ${_flash.name}', icon: Icon(switch (_flash) { FlashMode.off => Icons.flash_off, FlashMode.auto => Icons.flash_auto, FlashMode.always => Icons.flash_on, FlashMode.torch => Icons.highlight }), onPressed: _cycleFlash),
                if (_cams.length > 1) IconButton(tooltip: 'Switch camera', icon: const Icon(Icons.cameraswitch_outlined), onPressed: () => _open((_index + 1) % _cams.length)),
              ],
            ),
            body: SafeArea(
              child: Column(children: [
                Expanded(
                  child: _problem != null
                      ? StateMessage(icon: Icons.no_photography_outlined, title: 'Camera unavailable', body: _problem, action: OutlinedButton(onPressed: _gallery, child: const Text('Choose photos')))
                      : c == null || !c.value.isInitialized
                          ? const Center(child: CircularProgressIndicator())
                          : Center(child: CameraPreview(c)),
                ),
                Container(
                  color: Colors.black,
                  padding: const EdgeInsets.all(16),
                  child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                    IconButton(tooltip: 'Choose photos from gallery', color: Colors.white, icon: const Icon(Icons.photo_library_outlined), onPressed: _gallery),
                    Semantics(
                      button: true,
                      label: 'Take picture',
                      child: GestureDetector(onTap: _shoot, child: Container(width: 72, height: 72, decoration: BoxDecoration(shape: BoxShape.circle, color: _busy ? Colors.grey : Colors.white, border: Border.all(color: Colors.white54, width: 4)))),
                    ),
                    FilledButton(onPressed: widget.session.isEmpty ? null : _review, child: Text(widget.session.isEmpty ? 'Done' : 'Done (${widget.session.pageCount})')),
                  ]),
                ),
              ]),
            ),
          );
        },
      );
}
