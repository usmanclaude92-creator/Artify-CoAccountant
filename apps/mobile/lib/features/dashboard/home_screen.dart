import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/network/api_error.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../documents/documents_repository.dart';
import '../scanner/scan_session.dart';
import '../scanner/scanner_screen.dart';
import '../upload/queue_screen.dart';
import '../upload/upload_form_screen.dart';
import '../upload/upload_models.dart';
import '../workspace/workspace_screens.dart';

/// Real numbers only: the upload queue on this phone and the documents the server reports. Nothing is estimated.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.services, required this.openDocuments});
  final AppServices services;
  final VoidCallback openDocuments;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  List<DocRow> _recent = const [];
  int? _serverTotal;
  String? _error;
  bool _loading = true;

  AppServices get s => widget.services;
  String get _company => s.orgs.company!.id;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final me = s.auth.me;
      final page = await s.docs.list(_company, limit: 5, createdBy: me?.userId);
      if (mounted) {
        setState(() {
          _recent = page.items;
          _serverTotal = page.total;
          _error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.isNetwork ? 'Offline: showing what is on this phone.' : e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _startScan({ScanSession? resume}) async {
    final session = resume ?? ScanSession(workDir: s.scanDir);
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => ScannerScreen(services: s, session: session)));
    if (mounted) _load();
  }

  Future<void> _upload() async {
    final files = await s.imports.pickFiles();
    if (files.isEmpty || !mounted) return;
    for (final f in files) {
      if (!mounted) return;
      await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => UploadFormScreen(services: s, filePath: f.path, fileName: f.name)));
    }
    if (mounted) _load();
  }

  @override
  Widget build(BuildContext context) {
    final me = s.auth.me;
    final org = s.orgs.organization, company = s.orgs.company!;
    return ListenableBuilder(
      listenable: Listenable.merge([s.queue, s.runner]),
      builder: (context, _) {
        final q = s.queue.forCompany(company.id).toList();
        final today = DateTime.now().toUtc().subtract(const Duration(hours: 24));
        final pending = q.where((i) => i.isActive || i.state == UploadState.draft).length;
        final failed = q.where((i) => i.state == UploadState.failed || i.state == UploadState.quarantined).length;
        final doneToday = q.where((i) => i.state == UploadState.completed && i.updatedAt.isAfter(today)).length;
        return Scaffold(
          appBar: AppBar(title: const Text('Artify'), actions: [
            IconButton(tooltip: 'Upload queue', icon: Badge(isLabelVisible: pending + failed > 0, label: Text('${pending + failed}'), child: const Icon(Icons.cloud_upload_outlined)), onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => QueueScreen(services: s)))),
          ]),
          body: RefreshIndicator(
            onRefresh: _load,
            child: ListView(padding: const EdgeInsets.all(16), children: [
              Text('Hello, ${me?.displayName.split(' ').first ?? 'there'}', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 4),
              Card(
                child: ListTile(
                  leading: const Icon(Icons.business_center_outlined),
                  title: Text(company.name),
                  subtitle: Text('${org?.organizationName ?? ''}${s.orgs.roleInCompany == null ? '' : ' · ${s.orgs.roleInCompany!.replaceAll('_', ' ')}'}'),
                  trailing: TextButton(onPressed: s.orgs.leaveCompany, child: const Text('Switch')),
                ),
              ),
              if (_error != null) ...[const SizedBox(height: 8), Banner2(_error!, tone: Tone.warning)],
              const SizedBox(height: 16),
              Row(children: [
                Expanded(child: _Action(icon: Icons.document_scanner_outlined, label: 'Scan document', primary: true, onTap: _startScan)),
                const SizedBox(width: 12),
                Expanded(child: _Action(icon: Icons.upload_file_outlined, label: 'Upload file', onTap: _upload)),
              ]),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(child: _Action(icon: Icons.folder_outlined, label: 'Browse folders', onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => WorkspaceBrowseScreen(services: s))))),
                const SizedBox(width: 12),
                Expanded(child: _Action(icon: Icons.history, label: 'Recent documents', onTap: widget.openDocuments)),
              ]),
              const SizedBox(height: 24),
              Text('Uploads on this phone', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              Wrap(spacing: 12, runSpacing: 12, children: [
                _Stat('Waiting', '$pending', Icons.schedule, Tone.warning),
                _Stat('Verified in last 24 h', '$doneToday', Icons.cloud_done_outlined, Tone.success),
                _Stat('Failed', '$failed', Icons.error_outline, failed > 0 ? Tone.danger : Tone.neutral),
                if (_serverTotal != null) _Stat('Sent by you (all time)', '$_serverTotal', Icons.folder_copy_outlined, Tone.info),
              ]),
              FutureBuilder<List<String>>(
                future: ScanSession.listDrafts(s.scanDir),
                builder: (context, snap) => (snap.data ?? const []).isEmpty
                    ? const SizedBox.shrink()
                    : Padding(padding: const EdgeInsets.only(top: 16), child: Card(child: ListTile(leading: const Icon(Icons.edit_note), title: Text('${snap.data!.length} unfinished scan${snap.data!.length == 1 ? '' : 's'}'), subtitle: const Text('Saved on this phone'), trailing: TextButton(onPressed: () async {
                          final draft = await ScanSession.loadDraft(s.scanDir, snap.data!.first);
                          if (draft != null && mounted) _startScan(resume: draft);
                        }, child: const Text('Resume'))))),
              ),
              const SizedBox(height: 24),
              Text('Recent activity', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              if (_loading && _recent.isEmpty) const LinearProgressIndicator(),
              if (!_loading && _recent.isEmpty && q.isEmpty) const Padding(padding: EdgeInsets.all(16), child: Text('Nothing sent yet. Scan or upload your first document.')),
              for (final i in q.where((i) => !i.isFinished || i.state == UploadState.completed).toList().reversed.take(3))
                ListTile(contentPadding: EdgeInsets.zero, leading: Icon(iconOf(i.state)), title: Text(i.displayName, overflow: TextOverflow.ellipsis), subtitle: Text(i.stageLabel(online: true))),
              for (final d in _recent) ListTile(contentPadding: EdgeInsets.zero, leading: const Icon(Icons.description_outlined), title: Text(d.displayName, overflow: TextOverflow.ellipsis), subtitle: Text('${fmtWhen(d.createdAt)} · verified in cloud storage')),
            ]),
          ),
        );
      },
    );
  }
}

class _Action extends StatelessWidget {
  const _Action({required this.icon, required this.label, required this.onTap, this.primary = false});
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool primary;
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Material(
      color: primary ? cs.primary : cs.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8), side: BorderSide(color: primary ? cs.primary : cs.outline)),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: onTap,
        child: Padding(padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 12), child: Column(children: [Icon(icon, size: 28, color: primary ? cs.onPrimary : cs.primary), const SizedBox(height: 8), Text(label, textAlign: TextAlign.center, style: TextStyle(fontWeight: FontWeight.w600, color: primary ? cs.onPrimary : cs.onSurface))])),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value, this.icon, this.tone);
  final String label;
  final String value;
  final IconData icon;
  final Tone tone;
  @override
  Widget build(BuildContext context) => SizedBox(
        width: (MediaQuery.sizeOf(context).width - 56) / 2,
        child: Card(child: Padding(padding: const EdgeInsets.all(12), child: Row(children: [Icon(icon, color: toneColor(context, tone)), const SizedBox(width: 10), Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(value, style: Theme.of(context).textTheme.titleLarge), Text(label, style: Theme.of(context).textTheme.bodySmall)]))]))),
      );
}
