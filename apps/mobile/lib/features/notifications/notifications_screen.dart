import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/network/api_error.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'notification_center.dart';

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  String get _company => widget.services.orgs.company!.id;
  NotificationCenter get nc => widget.services.notices;

  @override
  void initState() {
    super.initState();
    nc.refresh(_company);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: Listenable.merge([nc, widget.services.queue]),
        builder: (context, _) {
          final list = nc.notices(_company);
          return Scaffold(
            appBar: AppBar(title: const Text('Notifications'), actions: [
              IconButton(tooltip: 'Mark all as read', icon: const Icon(Icons.done_all), onPressed: () => nc.markAllRead(_company)),
              IconButton(tooltip: 'Notification settings', icon: const Icon(Icons.tune), onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => NotificationPrefsScreen(services: widget.services)))),
            ]),
            body: RefreshIndicator(
              onRefresh: () => nc.refresh(_company),
              child: ListView(padding: const EdgeInsets.all(16), children: [
                if (nc.error != null) ...[Banner2(nc.error!, tone: Tone.warning), const SizedBox(height: 12)],
                const Banner2('Notices appear while the app is open. Push alerts to your lock screen are not set up yet.', tone: Tone.info),
                const SizedBox(height: 12),
                if (list.isEmpty) const Padding(padding: EdgeInsets.only(top: 40), child: StateMessage(icon: Icons.notifications_none, title: 'Nothing new', body: 'Upload results and document updates will show here.')),
                for (final n in list)
                  Card(
                    margin: const EdgeInsets.only(bottom: 8),
                    child: ListTile(
                      leading: Icon(switch (n.kind) { NoticeKind.uploadCompleted => Icons.cloud_done_outlined, NoticeKind.uploadFailed => Icons.error_outline, NoticeKind.quarantined => Icons.gpp_maybe_outlined, NoticeKind.documentEvent => Icons.description_outlined, NoticeKind.serverNotification => Icons.assignment_outlined }),
                      title: Text(n.title, style: TextStyle(fontWeight: nc.isUnread(n) ? FontWeight.w700 : FontWeight.w400)),
                      subtitle: Text('${n.detail}\n${fmtWhen(n.at)}'),
                      isThreeLine: true,
                    ),
                  ),
              ]),
            ),
          );
        },
      );
}

class NotificationPrefsScreen extends StatelessWidget {
  const NotificationPrefsScreen({super.key, required this.services});
  final AppServices services;
  @override
  Widget build(BuildContext context) {
    final nc = services.notices;
    return ListenableBuilder(
      listenable: nc,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text('Notification settings')),
        body: ListView(children: [
          SwitchListTile(title: const Text('Upload results'), subtitle: const Text('Verified, failed or quarantined uploads from this phone'), value: nc.showUploadNotices, onChanged: nc.setShowUploadNotices),
          SwitchListTile(title: const Text('Document updates'), subtitle: const Text('Processing results and workspace changes reported by the server'), value: nc.showServerNotices, onChanged: nc.setShowServerNotices),
          const _EmailPref(),
          ListTile(leading: const Icon(Icons.notifications_off_outlined), title: const Text('Push alerts'), subtitle: Text(services.push.isAvailable ? 'On' : 'Not available yet. No push service has been set up for this app.')),
          const ListTile(leading: Icon(Icons.lock_outline), title: Text('Lock-screen privacy'), subtitle: Text('Notices never contain amounts, supplier or customer names or document content.')),
        ]),
      ),
    );
  }
}

/// Whether the server also e-mails the person about assignments and closings. The in-app notice stays either way.
class _EmailPref extends StatefulWidget {
  const _EmailPref();
  @override
  State<_EmailPref> createState() => _EmailPrefState();
}

class _EmailPrefState extends State<_EmailPref> {
  bool? _on;
  String? _error;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_on == null && _error == null) _load();
  }

  AppServices get _services => context.findAncestorWidgetOfExactType<NotificationPrefsScreen>()!.services;

  Future<void> _load() async {
    try {
      final j = await _services.api.getJson('/notifications/preferences');
      if (mounted) setState(() => _on = j['email_enabled'] as bool? ?? true);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _set(bool v) async {
    try {
      await _services.api.putJson('/notifications/preferences', {'email_enabled': v});
      if (mounted) setState(() => _on = v);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) => SwitchListTile(
        title: const Text('E-mail about tasks and closings'),
        subtitle: Text(_error ?? 'Assignments, reviews and closing results are also sent to your e-mail address. The notice stays here either way.'),
        value: _on ?? true,
        onChanged: _on == null ? null : _set,
      );
}
