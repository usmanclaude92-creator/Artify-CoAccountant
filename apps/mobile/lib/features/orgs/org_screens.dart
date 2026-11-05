import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../auth/auth_models.dart';
import '../upload/upload_models.dart';
import 'org_models.dart';

String roleLabel(String? r) => r == null ? 'Member' : r.replaceAll('_', ' ').replaceFirstMapped(RegExp(r'^.'), (m) => m[0]!.toUpperCase());

class OrgSelectScreen extends StatelessWidget {
  const OrgSelectScreen({super.key, required this.services});
  final AppServices services;

  @override
  Widget build(BuildContext context) {
    final orgs = services.orgs;
    return ListenableBuilder(
      listenable: orgs,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text('Choose organization'), actions: [IconButton(tooltip: 'Sign out', icon: const Icon(Icons.logout), onPressed: () => confirmSignOut(context, services))]),
        body: orgs.organizations.isEmpty
            ? const StateMessage(icon: Icons.business_outlined, title: 'No organization yet', body: 'Your account is not a member of any organization. Ask your accountant or administrator to invite you.')
            : ListView.separated(
                padding: const EdgeInsets.all(16),
                itemCount: orgs.organizations.length,
                separatorBuilder: (_, _) => const SizedBox(height: 8),
                itemBuilder: (_, i) => _OrgCard(m: orgs.organizations[i], onTap: () => orgs.selectOrganization(orgs.organizations[i])),
              ),
      ),
    );
  }
}

class _OrgCard extends StatelessWidget {
  const _OrgCard({required this.m, required this.onTap});
  final Membership m;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final active = m.organizationStatus == 'active';
    return Card(
      child: ListTile(
        minVerticalPadding: 12,
        leading: const Icon(Icons.apartment_outlined),
        title: Text(m.organizationName),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Wrap(spacing: 8, runSpacing: 4, children: [
            StatusPill(active ? 'Active' : roleLabel(m.organizationStatus), tone: active ? Tone.success : Tone.warning),
            Text('Your role: ${roleLabel(m.organizationRole ?? (m.companyRoles.isNotEmpty ? m.companyRoles.values.first : null))}'),
            if (m.companyRoles.isNotEmpty) Text('${m.companyRoles.length} compan${m.companyRoles.length == 1 ? 'y' : 'ies'} by direct access'),
          ]),
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: active ? onTap : null,
      ),
    );
  }
}

class CompanySelectScreen extends StatefulWidget {
  const CompanySelectScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<CompanySelectScreen> createState() => _CompanySelectScreenState();
}

class _CompanySelectScreenState extends State<CompanySelectScreen> {
  String _q = '';

  @override
  Widget build(BuildContext context) {
    final orgs = widget.services.orgs;
    return ListenableBuilder(
      listenable: orgs,
      builder: (context, _) {
        final list = orgs.companies.where((c) => _q.isEmpty || c.name.toLowerCase().contains(_q.toLowerCase()) || c.code.toLowerCase().contains(_q.toLowerCase())).toList();
        return Scaffold(
          appBar: AppBar(
            title: Text(orgs.organization?.organizationName ?? 'Companies'),
            actions: [
              if (orgs.organizations.length > 1) IconButton(tooltip: 'Switch organization', icon: const Icon(Icons.swap_horiz), onPressed: () => widget.services.orgs.setMemberships(widget.services.auth.me!.memberships)),
              IconButton(tooltip: 'Sign out', icon: const Icon(Icons.logout), onPressed: () => confirmSignOut(context, widget.services)),
            ],
          ),
          body: RefreshIndicator(
            onRefresh: orgs.loadCompanies,
            child: ListView(padding: const EdgeInsets.all(16), children: [
              if (orgs.deviceNotice != null) ...[Banner2(orgs.deviceNotice!, tone: Tone.warning), const SizedBox(height: 12)],
              if (orgs.error != null) ...[Banner2(orgs.error!), const SizedBox(height: 12)],
              TextField(decoration: const InputDecoration(prefixIcon: Icon(Icons.search), labelText: 'Search companies'), onChanged: (v) => setState(() => _q = v)),
              const SizedBox(height: 12),
              if (orgs.loading) const LinearProgressIndicator(),
              if (!orgs.loading && list.isEmpty) const Padding(padding: EdgeInsets.only(top: 48), child: StateMessage(icon: Icons.business_center_outlined, title: 'No companies to show', body: 'You can only see companies you have been given access to.')),
              for (final c in list) Padding(padding: const EdgeInsets.only(bottom: 8), child: _CompanyCard(c: c, onTap: () => orgs.selectCompany(c))),
            ]),
          ),
        );
      },
    );
  }
}

class _CompanyCard extends StatelessWidget {
  const _CompanyCard({required this.c, required this.onTap});
  final CompanyRef c;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Card(
        child: ListTile(
          minVerticalPadding: 12,
          leading: CircleAvatar(child: Text(c.name.isEmpty ? '?' : c.name[0].toUpperCase())),
          title: Text(c.name),
          subtitle: Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Wrap(spacing: 8, runSpacing: 4, children: [
              StatusPill(c.isActive ? 'Active' : roleLabel(c.status), tone: c.isActive ? Tone.success : Tone.warning),
              Text('${c.country} · ${c.baseCurrency}'),
              if (c.legalName != null && c.legalName != c.name) Text(c.legalName!),
            ]),
          ),
          trailing: const Icon(Icons.chevron_right),
          onTap: c.isActive ? onTap : null,
        ),
      );
}

Future<void> confirmSignOut(BuildContext context, AppServices s) async {
  final queued = s.queue.items.where((i) => i.isActive || i.state == UploadState.draft).length;
  final ok = await showDialog<bool>(
    context: context,
    builder: (_) => AlertDialog(
      title: const Text('Sign out?'),
      content: Text(queued > 0 ? '$queued document${queued == 1 ? ' is' : 's are'} still waiting to upload. Signing out removes them from this phone. Stay signed in to let them finish.' : 'You will need to sign in again to use the app.'),
      actions: [TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Stay signed in')), FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Sign out'))],
    ),
  );
  if (ok == true) {
    await s.auth.logout();
    await s.wipeLocalData();
  }
}
