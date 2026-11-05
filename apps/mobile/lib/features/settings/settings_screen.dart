import 'dart:io';

import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../notifications/notifications_screen.dart';
import '../orgs/org_screens.dart';
import '../upload/queue_screen.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool _bio = false;
  bool _bioAvailable = false;
  int _queueBytes = 0;
  int _cacheBytes = 0;

  AppServices get s => widget.services;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    final bio = await s.tokens.biometricEnabled();
    final avail = await s.biometrics.available;
    final q = await s.queue.bytesOnDevice();
    var c = 0;
    for (final d in [s.cacheDir, s.scanDir]) {
      if (await d.exists()) c += (await d.list(recursive: true).where((e) => e is File).cast<File>().asyncMap((f) => f.length()).fold<int>(0, (a, b) => a + b));
    }
    if (mounted) {
      setState(() {
        _bio = bio;
        _bioAvailable = avail;
        _queueBytes = q;
        _cacheBytes = c;
      });
    }
  }

  Future<void> _toggleBio(bool on) async {
    if (on && !await s.biometrics.authenticate('Turn on app lock')) return;
    await s.tokens.setBiometric(on);
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final me = s.auth.me;
    final org = s.orgs.organization, company = s.orgs.company;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(children: [
        ListTile(leading: const CircleAvatar(child: Icon(Icons.person_outline)), title: Text(me?.displayName ?? 'Signed in'), subtitle: Text(me?.email ?? '')),
        const Divider(),
        ListTile(leading: const Icon(Icons.apartment_outlined), title: Text(org?.organizationName ?? '—'), subtitle: Text('Your role: ${roleLabel(s.orgs.roleInCompany)}${company == null ? '' : '\nCompany: ${company.name}'}'), isThreeLine: company != null),
        if (s.orgs.organizations.length > 1) ListTile(leading: const Icon(Icons.swap_horiz), title: const Text('Switch organization'), onTap: () {
          s.orgs.clear();
          s.orgs.setMemberships(s.auth.me!.memberships);
        }),
        ListTile(leading: const Icon(Icons.business_center_outlined), title: const Text('Switch company'), onTap: s.orgs.leaveCompany),
        const Divider(),
        SwitchListTile(secondary: const Icon(Icons.fingerprint), title: const Text('Lock with fingerprint, face or passcode'), subtitle: Text(_bioAvailable ? 'Asks again when you come back to the app. You stay signed in; this only protects the phone.' : 'No fingerprint, face or passcode is set up on this phone.'), value: _bio, onChanged: _bioAvailable ? _toggleBio : null),
        ListTile(leading: const Icon(Icons.notifications_outlined), title: const Text('Notifications'), onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => NotificationsScreen(services: s)))),
        ListTile(leading: const Icon(Icons.cloud_upload_outlined), title: const Text('Upload queue'), subtitle: Text('${s.queue.items.where((i) => i.isActive).length} waiting · ${fmtBytes(_queueBytes)} stored on this phone'), onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => QueueScreen(services: s)))),
        ListTile(leading: const Icon(Icons.storage_outlined), title: const Text('Storage on this phone'), subtitle: Text('Queued documents ${fmtBytes(_queueBytes)} · scans and previews ${fmtBytes(_cacheBytes)}'), trailing: TextButton(onPressed: () async {
          for (final d in [s.cacheDir]) {
            if (await d.exists()) await d.delete(recursive: true);
          }
          await s.workspace.clear();
          _refresh();
        }, child: const Text('Clear cache'))),
        const Divider(),
        ListTile(leading: const Icon(Icons.privacy_tip_outlined), title: const Text('Privacy'), onTap: () => _info(context, 'Privacy', 'Documents you scan or choose are kept in this app\'s private storage until the Artify server has verified them, then removed from the queue. Tokens are kept in the phone\'s secure storage (Android Keystore / iOS Keychain). The app does not read your photos, files or contacts unless you pick them, and it does not contain any server, storage or AI credentials. Your accountant\'s organization decides who can see each document.')),
        ListTile(leading: const Icon(Icons.help_outline), title: const Text('Help and support'), onTap: () => _info(context, 'Help and support', 'If an upload fails, open the upload queue and read the reason; most problems are a missing folder permission or a file type the company does not accept. For anything else, contact your accountant or the administrator of your organization. Device and plan limits are set by the organization\'s subscription.')),
        ListTile(leading: const Icon(Icons.info_outline), title: const Text('About Artify Co-Accountant'), subtitle: Text('Version ${s.config.appVersion}${s.config.isConfigured ? '' : ' · no server configured'}'), onTap: () => _info(context, 'About', 'Artify Co-Accountant mobile ${s.config.appVersion}. A companion for capturing and sending documents. The accounting work happens in the Windows app.')),
        ListTile(leading: Icon(Icons.logout, color: toneColor(context, Tone.danger)), title: const Text('Sign out'), onTap: () => confirmSignOut(context, s)),
        const SizedBox(height: 24),
      ]),
    );
  }

  void _info(BuildContext context, String title, String body) => showDialog<void>(context: context, builder: (_) => AlertDialog(title: Text(title), content: SingleChildScrollView(child: Text(body)), actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close'))]));
}
