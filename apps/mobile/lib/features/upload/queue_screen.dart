import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'upload_models.dart';

Tone toneOf(UploadState s) => switch (s) {
      UploadState.completed => Tone.success,
      UploadState.failed || UploadState.quarantined => Tone.danger,
      UploadState.uploading || UploadState.verifying => Tone.info,
      UploadState.waiting || UploadState.draft => Tone.warning,
      UploadState.cancelled => Tone.neutral,
    };

IconData iconOf(UploadState s) => switch (s) {
      UploadState.completed => Icons.cloud_done_outlined,
      UploadState.failed => Icons.error_outline,
      UploadState.quarantined => Icons.gpp_maybe_outlined,
      UploadState.uploading => Icons.cloud_upload_outlined,
      UploadState.verifying => Icons.verified_outlined,
      UploadState.waiting => Icons.schedule,
      UploadState.draft => Icons.edit_note,
      UploadState.cancelled => Icons.cancel_outlined,
    };

/// Every document on its way to the cloud, with an honest state: saved on this phone, waiting, uploading, verified.
class QueueScreen extends StatelessWidget {
  const QueueScreen({super.key, required this.services});
  final AppServices services;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: Listenable.merge([services.queue, services.runner]),
        builder: (context, _) {
          final items = services.queue.items.reversed.toList();
          return Scaffold(
            appBar: AppBar(title: const Text('Upload queue'), actions: [
              IconButton(tooltip: 'Try uploading now', icon: const Icon(Icons.sync), onPressed: services.runner.kick),
            ]),
            body: items.isEmpty
                ? const StateMessage(icon: Icons.inbox_outlined, title: 'Nothing in the queue', body: 'Documents you scan or choose appear here until the server has verified them.')
                : FutureBuilder<bool>(
                    future: services.connectivity.isOnline,
                    builder: (context, snap) => ListView.separated(
                      padding: const EdgeInsets.all(16),
                      itemCount: items.length,
                      separatorBuilder: (_, _) => const SizedBox(height: 8),
                      itemBuilder: (_, i) => QueueTile(item: items[i], online: snap.data ?? true, services: services),
                    ),
                  ),
          );
        },
      );
}

class QueueTile extends StatelessWidget {
  const QueueTile({super.key, required this.item, required this.online, required this.services});
  final QueueItem item;
  final bool online;
  final AppServices services;

  @override
  Widget build(BuildContext context) {
    final label = item.stageLabel(online: online);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(source(item.source), size: 20),
            const SizedBox(width: 8),
            Expanded(child: Text(item.displayName, style: Theme.of(context).textTheme.titleSmall, overflow: TextOverflow.ellipsis)),
          ]),
          const SizedBox(height: 8),
          Wrap(spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
            StatusPill(label, tone: toneOf(item.state), icon: iconOf(item.state)),
            Text(fmtBytes(item.size)),
            if (item.pages != null) Text('${item.pages} pages'),
            if (item.attempts > 1) Text('attempt ${item.attempts}'),
          ]),
          if (item.state == UploadState.uploading || item.state == UploadState.verifying) Padding(padding: const EdgeInsets.only(top: 8), child: LinearProgressIndicator(value: item.progress == 0 ? null : item.progress)),
          if (item.errorMessage != null && item.state != UploadState.completed) Padding(padding: const EdgeInsets.only(top: 8), child: Text(item.errorMessage!, style: TextStyle(color: toneColor(context, item.state == UploadState.waiting ? Tone.warning : Tone.danger)))),
          Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            if (item.state == UploadState.draft) TextButton(onPressed: () => services.queue.submitDraft(item.id).then((_) => services.runner.kick()), child: const Text('Send')),
            if (item.state == UploadState.failed && item.errorCode != 'local_file_missing') TextButton(onPressed: () => services.queue.retry(item.id).then((_) => services.runner.kick()), child: const Text('Retry')),
            if (item.state == UploadState.failed && (item.errorCode?.contains('duplicate') ?? false)) TextButton(onPressed: () => services.queue.retry(item.id, asCopy: true).then((_) => services.runner.kick()), child: const Text('Upload as a copy')),
            if (item.state == UploadState.waiting && item.nextAttemptAt != null) TextButton(onPressed: () => services.queue.retry(item.id).then((_) => services.runner.kick()), child: const Text('Try now')),
            if (item.isActive || item.state == UploadState.draft) TextButton(onPressed: () => services.queue.cancel(item.id), child: const Text('Cancel')),
            if (item.isFinished || item.state == UploadState.failed || item.state == UploadState.quarantined) TextButton(onPressed: () => services.queue.remove(item.id), child: const Text('Remove')),
          ]),
        ]),
      ),
    );
  }

  static IconData source(UploadSource s) => switch (s) {
        UploadSource.scan => Icons.document_scanner_outlined,
        UploadSource.gallery => Icons.photo_outlined,
        UploadSource.file => Icons.description_outlined,
      };
}
