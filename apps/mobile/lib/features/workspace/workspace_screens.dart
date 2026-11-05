import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/network/api_error.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'workspace_models.dart';

/// Loads the company's configuration: cached copy first (instant, works offline), then revalidated with the server.
class WorkspaceLoader extends StatefulWidget {
  const WorkspaceLoader({super.key, required this.services, required this.builder});
  final AppServices services;
  final Widget Function(BuildContext, UploadConfig, Future<void> Function() refresh) builder;
  @override
  State<WorkspaceLoader> createState() => _WorkspaceLoaderState();
}

class _WorkspaceLoaderState extends State<WorkspaceLoader> {
  UploadConfig? _cfg;
  String? _error;
  bool _offlineCopy = false;
  bool _changed = false;

  String get _company => widget.services.orgs.company!.id;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final cached = await widget.services.workspace.cached(_company);
    if (mounted && cached != null) setState(() => _cfg = cached.config);
    await _refresh();
  }

  Future<void> _refresh() async {
    try {
      final r = await widget.services.workspace.refresh(_company);
      if (!mounted) return;
      setState(() {
        _cfg = r.config;
        _error = null;
        _offlineCopy = false;
        _changed = r.changed && _cfg != null;
      });
    } on ApiError catch (e) {
      if (!mounted) return;
      setState(() {
        _offlineCopy = _cfg != null;
        _error = _cfg != null ? (e.isNetwork ? 'Showing the last saved folders. The server cannot be reached.' : e.message) : e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cfg = _cfg;
    if (cfg == null) {
      return _error != null
          ? StateMessage(icon: Icons.folder_off_outlined, title: 'Folders unavailable', body: _error, action: FilledButton(onPressed: _refresh, child: const Text('Try again')))
          : const Center(child: CircularProgressIndicator());
    }
    return Column(children: [
      if (_offlineCopy && _error != null) Padding(padding: const EdgeInsets.fromLTRB(16, 8, 16, 0), child: Banner2(_error!, tone: Tone.warning)),
      if (_changed) Padding(padding: const EdgeInsets.fromLTRB(16, 8, 16, 0), child: const Banner2('This company\'s folders were updated. You are seeing the latest version.', tone: Tone.info)),
      Expanded(child: widget.builder(context, cfg, _refresh)),
    ]);
  }
}

/// Browse (and optionally choose) a destination. Only folders the server says the user may upload to are shown.
class WorkspaceBrowseScreen extends StatelessWidget {
  const WorkspaceBrowseScreen({super.key, required this.services, this.selecting = false});
  final AppServices services;
  final bool selecting;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(selecting ? 'Choose a folder' : 'Browse folders')),
        body: SafeArea(child: WorkspaceLoader(services: services, builder: (c, cfg, refresh) => FolderList(cfg: cfg, parentId: null, selecting: selecting, onRefresh: refresh))),
      );
}

class FolderList extends StatelessWidget {
  const FolderList({super.key, required this.cfg, required this.parentId, required this.selecting, required this.onRefresh});
  final UploadConfig cfg;
  final String? parentId;
  final bool selecting;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final children = cfg.children(parentId);
    final here = parentId == null ? null : cfg.byId(parentId!);
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(padding: const EdgeInsets.all(16), children: [
        if (here != null) ...[
          Text(cfg.pathTo(here.id).map((t) => t.label).join(' › '), style: Theme.of(context).textTheme.labelLarge),
          if (here.description.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text(here.description)),
          if (selecting) Padding(padding: const EdgeInsets.only(top: 12), child: FilledButton.icon(icon: const Icon(Icons.check), label: Text('Use “${here.label}”'), onPressed: () => Navigator.of(context).pop(here))),
          const SizedBox(height: 12),
        ],
        if (children.isEmpty && here == null) const StateMessage(icon: Icons.folder_open_outlined, title: 'No folders you can upload to', body: 'This company has no folders available to you yet. Ask your accountant to set up the workspace or to give you access.'),
        for (final t in children)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              leading: Icon(t.nodeType == 'section' ? Icons.folder_special_outlined : Icons.folder_outlined),
              title: Text(t.label),
              subtitle: t.description.isEmpty ? null : Text(t.description, maxLines: 2, overflow: TextOverflow.ellipsis),
              trailing: cfg.children(t.id).isEmpty ? (selecting ? const Icon(Icons.check_circle_outline) : null) : const Icon(Icons.chevron_right),
              onTap: () async {
                if (cfg.children(t.id).isEmpty) {
                  if (selecting) Navigator.of(context).pop(t);
                  return;
                }
                final picked = await Navigator.of(context).push<WsTarget>(MaterialPageRoute(builder: (_) => Scaffold(appBar: AppBar(title: Text(t.label)), body: SafeArea(child: FolderList(cfg: cfg, parentId: t.id, selecting: selecting, onRefresh: onRefresh)))));
                if (picked != null && context.mounted) Navigator.of(context).pop(picked);
              },
            ),
          ),
      ]),
    );
  }
}
