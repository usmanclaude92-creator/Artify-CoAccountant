import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

import '../../app_services.dart';
import '../../core/network/api_error.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../workspace/workspace_models.dart';
import 'documents_repository.dart';

/// "My documents": what the server lets this user see, newest first, loaded a page at a time.
class DocumentsScreen extends StatefulWidget {
  const DocumentsScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<DocumentsScreen> createState() => _DocumentsScreenState();
}

class _DocumentsScreenState extends State<DocumentsScreen> {
  final _items = <DocRow>[];
  int _total = 0;
  bool _loading = false;
  String? _error;
  String _q = '';
  bool _mine = true;
  UploadConfig? _cfg;
  final _scroll = ScrollController();

  String get _company => widget.services.orgs.company!.id;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 300) _more();
    });
    widget.services.workspace.cached(_company).then((c) => mounted ? setState(() => _cfg = c?.config) : null);
    _reload();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _reload() async {
    _items.clear();
    _total = 0;
    await _more();
  }

  Future<void> _more() async {
    if (_loading || (_total > 0 && _items.length >= _total)) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final me = widget.services.auth.me;
      final page = await widget.services.docs.list(_company, offset: _items.length, createdBy: _mine ? me?.userId : null, query: _q);
      if (!mounted) return;
      setState(() {
        _items.addAll(page.items);
        _total = page.total;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.isNetwork ? 'The server cannot be reached. Pull down to try again.' : e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Documents')),
        body: RefreshIndicator(
          onRefresh: _reload,
          child: ListView(controller: _scroll, padding: const EdgeInsets.all(16), children: [
            TextField(decoration: const InputDecoration(prefixIcon: Icon(Icons.search), labelText: 'Search by name'), textInputAction: TextInputAction.search, onSubmitted: (v) {
              _q = v;
              _reload();
            }),
            const SizedBox(height: 8),
            SegmentedButton<bool>(segments: const [ButtonSegment(value: true, label: Text('Sent by me')), ButtonSegment(value: false, label: Text('All I can see'))], selected: {_mine}, onSelectionChanged: (s) {
              _mine = s.first;
              _reload();
            }),
            const SizedBox(height: 12),
            if (_error != null) Banner2(_error!),
            if (!_loading && _items.isEmpty && _error == null) const Padding(padding: EdgeInsets.only(top: 48), child: StateMessage(icon: Icons.folder_open_outlined, title: 'No documents yet', body: 'Documents you send appear here once the server has stored them.')),
            for (final d in _items)
              Card(
                margin: const EdgeInsets.only(bottom: 8),
                child: ListTile(
                  leading: Icon(d.extension == 'pdf' ? Icons.picture_as_pdf_outlined : Icons.description_outlined),
                  title: Text(d.displayName, overflow: TextOverflow.ellipsis),
                  subtitle: Text('${fmtWhen(d.createdAt)} · ${fmtBytes(d.sizeBytes)}${d.createdByName == null ? '' : ' · ${d.createdByName}'}\n${_cfg == null ? '' : _cfg!.pathTo(d.nodeId).map((t) => t.label).join(' › ')}'),
                  isThreeLine: true,
                  trailing: d.status == 'active' ? null : StatusPill(d.status, tone: Tone.warning),
                  onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => DocumentDetailScreen(services: widget.services, doc: d, cfg: _cfg))),
                ),
              ),
            if (_loading) const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator())),
            if (_items.isNotEmpty && _items.length < _total && !_loading) Center(child: TextButton(onPressed: _more, child: Text('Load more (${_total - _items.length} left)'))),
          ]),
        ),
      );
}

class DocumentDetailScreen extends StatefulWidget {
  const DocumentDetailScreen({super.key, required this.services, required this.doc, this.cfg});
  final AppServices services;
  final DocRow doc;
  final UploadConfig? cfg;
  @override
  State<DocumentDetailScreen> createState() => _DocumentDetailScreenState();
}

class _DocumentDetailScreenState extends State<DocumentDetailScreen> {
  late DocRow _doc = widget.doc;
  Map<String, dynamic>? _processing;
  List<Map<String, dynamic>> _versions = const [];
  String? _error;
  bool _loadingPreview = false;
  File? _previewFile;

  String get _company => widget.services.orgs.company!.id;

  @override
  void initState() {
    super.initState();
    widget.services.docs.processing(_company, _doc.id).then((v) => mounted ? setState(() => _processing = v) : null);
    widget.services.docs.versions(_company, _doc.id).then((v) => mounted ? setState(() => _versions = v) : null).catchError((_) {});
  }

  Future<void> _preview() async {
    setState(() {
      _loadingPreview = true;
      _error = null;
    });
    try {
      final bytes = await widget.services.docs.content(_company, _doc.id);
      final dir = await widget.services.cacheDir.create(recursive: true);
      final f = File(p.join(dir.path, 'preview-${_doc.id}.${_doc.extension}'));
      await f.writeAsBytes(bytes, flush: true);
      if (mounted) setState(() => _previewFile = f);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loadingPreview = false);
    }
  }

  Future<void> _rename() async {
    final c = TextEditingController(text: _doc.displayName);
    final name = await showDialog<String>(context: context, builder: (_) => AlertDialog(title: const Text('Rename'), content: TextField(controller: c, autofocus: true), actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')), FilledButton(onPressed: () => Navigator.pop(context, c.text), child: const Text('Save'))]));
    if (name == null || name.trim().isEmpty) return;
    try {
      final d = await widget.services.docs.rename(_company, _doc, name);
      if (mounted) setState(() => _doc = d);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.isForbiddenOrHidden ? 'You do not have permission to rename this document.' : e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cfg = widget.cfg;
    final pr = _processing;
    final prStatus = pr == null ? null : ((pr['job'] is Map ? (pr['job'] as Map)['status'] : pr['status']) as String?);
    return Scaffold(
      appBar: AppBar(title: const Text('Document'), actions: [IconButton(tooltip: 'Rename', icon: const Icon(Icons.edit_outlined), onPressed: _rename)]),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(16), children: [
          Text(_doc.displayName, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Wrap(spacing: 8, runSpacing: 4, children: [
            const StatusPill('Verified in cloud storage', tone: Tone.success, icon: Icons.cloud_done_outlined),
            StatusPill(prStatus == null ? 'Not processed' : 'Processing: ${prStatus.replaceAll('_', ' ')}', tone: prStatus == null ? Tone.neutral : (prStatus == 'failed' ? Tone.danger : Tone.info)),
          ]),
          const SizedBox(height: 16),
          if (_error != null) ...[Banner2(_error!), const SizedBox(height: 12)],
          _kv('Uploaded', fmtWhen(_doc.createdAt)),
          _kv('Uploaded by', _doc.createdByName ?? '—'),
          _kv('Type', _doc.extension.toUpperCase()),
          _kv('Size', fmtBytes(_doc.sizeBytes)),
          _kv('Location', cfg == null ? '—' : cfg.pathTo(_doc.nodeId).map((t) => t.label).join(' › ')),
          _kv('Category', cfg == null || _doc.categoryId == null ? '—' : (cfg.categories.where((c) => c.id == _doc.categoryId).firstOrNull?.name ?? '—')),
          _kv('Version', '${_doc.currentVersionNo}${_versions.length > 1 ? ' of ${_versions.length}' : ''}'),
          const SizedBox(height: 16),
          if (_previewFile != null && ['jpg', 'jpeg', 'png', 'bmp'].contains(_doc.extension)) ClipRRect(borderRadius: BorderRadius.circular(8), child: InteractiveViewer(child: Image.file(_previewFile!)))
          else if (_previewFile != null) const Banner2('The file was downloaded for this session. This version of the app shows pictures inside the app; open other file types with another app.', tone: Tone.info)
          else
            OutlinedButton.icon(icon: _loadingPreview ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.visibility_outlined), label: Text(_doc.previewable ? 'Open preview' : 'Download'), onPressed: _loadingPreview ? null : _preview),
        ]),
      ),
    );
  }

  Widget _kv(String k, String v) => Padding(padding: const EdgeInsets.symmetric(vertical: 4), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [SizedBox(width: 110, child: Text(k, style: Theme.of(context).textTheme.labelMedium)), Expanded(child: Text(v))]));
}
