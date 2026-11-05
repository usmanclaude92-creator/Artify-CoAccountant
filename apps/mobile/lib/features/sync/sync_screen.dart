import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'sync_service.dart';

class SyncScreen extends StatefulWidget {
  const SyncScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<SyncScreen> createState() => _SyncScreenState();
}

class _SyncScreenState extends State<SyncScreen> {
  SyncService get sync => widget.services.sync;
  String get _org => widget.services.orgs.organization!.organizationId;
  String get _company => widget.services.orgs.company!.id;

  @override
  void initState() {
    super.initState();
    sync.refresh(_org, _company);
  }

  static String _label(SyncState s) => switch (s) {
    SyncState.pending => 'Pending',
    SyncState.uploading => 'Uploading',
    SyncState.failed => 'Failed',
    SyncState.conflictDetected => 'Conflict detected',
    SyncState.synchronized => 'Synchronized',
  };
  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: Listenable.merge([sync, widget.services.queue]),
    builder: (context, _) {
      final v = sync.view;
      return Scaffold(
        appBar: AppBar(title: const Text('Synchronization')),
        body: RefreshIndicator(
          onRefresh: () => sync.refresh(_org, _company),
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (v == null)
                const Padding(
                  padding: EdgeInsets.all(24),
                  child: Center(child: CircularProgressIndicator()),
                )
              else ...[
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _label(v.state),
                          key: const Key('sync-state'),
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(v.label),
                        const SizedBox(height: 12),
                        Text(
                          'Last check with the server: ${v.lastPullAt == null ? 'never' : fmtWhen(v.lastPullAt!)}',
                        ),
                        Text(
                          'Documents waiting / uploading / failed: ${v.waiting} / ${v.uploading} / ${v.failed}',
                        ),
                        Text(
                          'Open conflicts (counted by the server): ${v.openConflicts ?? 'not confirmed'}',
                        ),
                        if (v.knownItems.isNotEmpty)
                          Text(
                            'Known on this phone: ${v.knownItems.entries.map((e) => '${e.value} ${e.key}').join(' · ')}',
                          ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: sync.busy
                      ? null
                      : () => sync.checkNow(_org, _company),
                  icon: const Icon(Icons.sync),
                  label: Text(
                    sync.busy ? 'Checking…' : 'Check with the server',
                  ),
                ),
              ],
              if (sync.message != null) ...[
                const SizedBox(height: 12),
                Banner2(sync.message!, tone: Tone.info),
              ],
              if (sync.conflicts.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text(
                  'Conflicts',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                for (final c in sync.conflicts)
                  Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${c.itemKind}: ${c.isLocked ? 'this record is locked on the server' : 'the server has a newer version'}',
                          ),
                          Text(
                            'Your copy: revision ${c.baseRevision ?? '?'} · Server: ${c.serverRevision ?? '?'}',
                          ),
                          const SizedBox(height: 8),
                          Wrap(
                            spacing: 8,
                            children: [
                              OutlinedButton(
                                onPressed: () => sync.decide(
                                  _org,
                                  _company,
                                  c,
                                  'keep_server',
                                ),
                                child: const Text('Keep the server copy'),
                              ),
                              if (!c.isLocked)
                                OutlinedButton(
                                  onPressed: () => sync.decide(
                                    _org,
                                    _company,
                                    c,
                                    'apply_local',
                                    note: 'Apply my change on top of the newer version',
                                  ),
                                  child: const Text(
                                    'Apply my change on the new version',
                                  ),
                                ),
                              OutlinedButton(
                                onPressed: () => sync.decide(
                                  _org,
                                  _company,
                                  c,
                                  'discarded',
                                  note: 'Not needed any more',
                                ),
                                child: const Text('Discard my change'),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
              const SizedBox(height: 16),
              const Banner2(
                '“Synchronized” appears only when this phone has read the server’s changes, nothing is waiting or failed, and the server reports no open conflicts. Resolving a conflict records your decision; it does not change the record.',
                tone: Tone.info,
              ),
            ],
          ),
        ),
      );
    },
  );
}
