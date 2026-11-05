import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:uuid/uuid.dart';

import 'enhance.dart';
import 'geometry.dart';
import 'page_renderer.dart';
import 'pdf_builder.dart';
import 'scan_models.dart';

typedef PageDetector = Future<Quad?> Function(String path);
typedef PageRenderer = Future<Uint8List> Function(ScanPage page, PdfQuality quality);

/// A scan in progress: an ordered list of pages that ends as ONE PDF. Pages can be added, retaken, rotated, re-cropped,
/// re-ordered and deleted; the original photos are never altered. A session can be saved as a draft and resumed after
/// the app is closed.
class ScanSession extends ChangeNotifier {
  ScanSession({required this.workDir, PageDetector? detector, PageRenderer? renderer, Uuid? uuid, String? id, List<ScanPage>? pages})
      : id = id ?? const Uuid().v4(),
        _detector = detector ?? detectInFile,
        _renderer = renderer ?? renderPage,
        _uuid = uuid ?? const Uuid(),
        _pages = [...?pages];

  final String id;
  final Directory workDir;
  final PageDetector _detector;
  final PageRenderer _renderer;
  final Uuid _uuid;
  final List<ScanPage> _pages;

  PdfQuality quality = PdfQuality.standard;
  String name = '';

  List<ScanPage> get pages => List.unmodifiable(_pages);
  int get pageCount => _pages.length;
  bool get isEmpty => _pages.isEmpty;

  Directory get _dir => Directory(p.join(workDir.path, id));

  /// Adds a freshly captured photo as the next page. The page outline is found automatically; when it cannot be, the
  /// whole picture is used and the person can drag the corners.
  Future<ScanPage> addCaptured(String path, {int? at}) async {
    final stored = await _keep(path);
    final quad = await _detector(stored);
    final page = ScanPage(id: _uuid.v4(), rawPath: stored, quad: quad ?? Quad.full(), autoDetected: quad != null);
    _pages.insert(at ?? _pages.length, page);
    notifyListeners();
    return page;
  }

  /// Replaces the photo of an existing page (retake) and keeps its position.
  Future<void> retake(String pageId, String newPath) async {
    final i = _index(pageId);
    final stored = await _keep(newPath);
    final quad = await _detector(stored);
    final old = _pages[i];
    _pages[i] = ScanPage(id: old.id, rawPath: stored, quad: quad ?? Quad.full(), autoDetected: quad != null);
    await _forget(old.rawPath);
    notifyListeners();
  }

  Future<void> delete(String pageId) async {
    final i = _index(pageId);
    final gone = _pages.removeAt(i);
    await _forget(gone.rawPath);
    notifyListeners();
  }

  void rotate(String pageId, {bool clockwise = true}) {
    final i = _index(pageId);
    _pages[i] = _pages[i].copyWith(rotation: (_pages[i].rotation + (clockwise ? 90 : 270)) % 360);
    notifyListeners();
  }

  void reorder(int oldIndex, int newIndex) {
    if (oldIndex < 0 || oldIndex >= _pages.length) return;
    final target = newIndex > oldIndex ? newIndex - 1 : newIndex; // same convention as ReorderableListView
    final page = _pages.removeAt(oldIndex);
    _pages.insert(target.clamp(0, _pages.length), page);
    notifyListeners();
  }

  void setQuad(String pageId, Quad q) {
    if (!q.isUsable) return;
    final i = _index(pageId);
    _pages[i] = _pages[i].copyWith(quad: q, autoDetected: false);
    notifyListeners();
  }

  void useFullPicture(String pageId) {
    final i = _index(pageId);
    _pages[i] = _pages[i].copyWith(quad: Quad.full(), autoDetected: false);
    notifyListeners();
  }

  void setAdjust(String pageId, Adjust a) {
    final i = _index(pageId);
    _pages[i] = _pages[i].copyWith(adjust: a);
    notifyListeners();
  }

  /// Applies one clean-up mode to every page (the usual choice for a multi-page statement).
  void setModeForAll(ScanMode mode) {
    for (var i = 0; i < _pages.length; i++) {
      _pages[i] = _pages[i].copyWith(adjust: _pages[i].adjust.copyWith(mode: mode));
    }
    notifyListeners();
  }

  void setQuality(PdfQuality q) {
    quality = q;
    notifyListeners();
  }

  /// The pages as they will appear in the PDF, in order.
  Future<List<Uint8List>> renderAll() async => [for (final pg in _pages) await _renderer(pg, quality)];

  /// One PDF with every page in order.
  Future<Uint8List> toPdf() async {
    if (_pages.isEmpty) throw StateError('Scan at least one page first.');
    return buildPdf(await renderAll(), title: name.isEmpty ? 'Scan' : name);
  }

  // ---- drafts: survive closing the app
  Future<void> saveDraft() async {
    await _dir.create(recursive: true);
    await File(p.join(_dir.path, 'draft.json')).writeAsString(jsonEncode({'id': id, 'name': name, 'quality': quality.name, 'pages': [for (final pg in _pages) pg.toJson()]}));
  }

  static Future<ScanSession?> loadDraft(Directory workDir, String id, {PageDetector? detector, PageRenderer? renderer}) async {
    final f = File(p.join(workDir.path, id, 'draft.json'));
    if (!await f.exists()) return null;
    final j = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
    final pages = [for (final e in (j['pages'] as List)) ScanPage.fromJson(e as Map<String, dynamic>)].where((pg) => File(pg.rawPath).existsSync()).toList();
    return ScanSession(workDir: workDir, id: id, pages: pages, detector: detector, renderer: renderer)
      ..name = (j['name'] as String?) ?? ''
      ..quality = PdfQuality.values.firstWhere((q) => q.name == j['quality'], orElse: () => PdfQuality.standard);
  }

  /// Draft ids that can be resumed (newest first).
  static Future<List<String>> listDrafts(Directory workDir) async {
    if (!await workDir.exists()) return [];
    final out = <(DateTime, String)>[];
    await for (final e in workDir.list()) {
      final f = File(p.join(e.path, 'draft.json'));
      if (e is Directory && await f.exists()) out.add((await f.lastModified(), p.basename(e.path)));
    }
    out.sort((a, b) => b.$1.compareTo(a.$1));
    return [for (final e in out) e.$2];
  }

  /// Throws the scan away: removes the photos and the draft.
  Future<void> discard() async {
    _pages.clear();
    if (await _dir.exists()) await _dir.delete(recursive: true);
    notifyListeners();
  }

  int _index(String pageId) {
    final i = _pages.indexWhere((e) => e.id == pageId);
    if (i < 0) throw StateError('That page is no longer part of the scan.');
    return i;
  }

  /// Copies the camera's photo into the session folder (the camera's own temp file may be cleaned by the OS).
  Future<String> _keep(String path) async {
    await _dir.create(recursive: true);
    final dest = p.join(_dir.path, '${_uuid.v4()}${p.extension(path).isEmpty ? '.jpg' : p.extension(path)}');
    await File(path).copy(dest);
    return dest;
  }

  Future<void> _forget(String path) async {
    final f = File(path);
    if (p.isWithin(_dir.path, path) && await f.exists()) await f.delete();
  }
}
