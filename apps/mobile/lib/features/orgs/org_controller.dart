import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/network/api_client.dart';
import '../../core/network/api_error.dart';
import '../auth/auth_models.dart';
import 'org_models.dart';

/// The active organization and company. Lists come from the server for THIS user (RLS-scoped); the app never builds a
/// company id of its own, and switching never signs the user out. Every company screen still asks the server again.
class OrgController extends ChangeNotifier {
  OrgController({required this.api, required this.prefs, this.onOrganizationSelected});
  final ApiClient api;
  final SharedPreferences prefs;
  final Future<void> Function(String organizationId)? onOrganizationSelected;

  static const _kOrg = 'sel.org';
  static const _kCompany = 'sel.company';

  List<Membership> organizations = const [];
  Membership? organization;
  List<CompanyRef> companies = const [];
  CompanyRef? company;
  bool loading = false;
  String? error;

  /// Set when this phone could not be registered for the organization (for example the plan's device limit is reached).
  String? deviceNotice;

  void setMemberships(List<Membership> list) {
    organizations = list.where((m) => m.organizationStatus != 'archived').toList();
    final saved = prefs.getString(_kOrg);
    organization = organizations.where((m) => m.organizationId == saved).firstOrNull;
    if (organization == null && organizations.length == 1) organization = organizations.first;
    api.organizationId = organization?.organizationId;
    notifyListeners();
  }

  Future<void> selectOrganization(Membership m) async {
    organization = m;
    company = null;
    companies = const [];
    error = null;
    deviceNotice = null;
    api.organizationId = m.organizationId;
    await prefs.setString(_kOrg, m.organizationId);
    await prefs.remove(_kCompany);
    notifyListeners();
    try {
      await onOrganizationSelected?.call(m.organizationId);
    } on ApiError catch (e) {
      deviceNotice = e.isEntitlement ? 'This organization has reached its limit of registered devices. Ask the administrator of ${m.organizationName} to free one or change the plan.' : e.message;
      notifyListeners();
    }
    await loadCompanies();
  }

  Future<void> loadCompanies() async {
    final org = organization;
    if (org == null) return;
    loading = true;
    notifyListeners();
    try {
      final all = <CompanyRef>[];
      var offset = 0;
      while (true) {
        final page = await api.getJson('/orgs/${org.organizationId}/companies', query: {'limit': '100', 'offset': '$offset'});
        final items = ((page['items'] as List?) ?? []).map((e) => CompanyRef.fromJson(e as Map<String, dynamic>)).toList();
        all.addAll(items);
        offset += items.length;
        if (items.isEmpty || offset >= ((page['total'] as num?)?.toInt() ?? 0)) break;
      }
      companies = all.where((c) => c.status != 'archived').toList();
      final saved = prefs.getString(_kCompany);
      company = companies.where((c) => c.id == saved).firstOrNull;
      error = null;
    } on ApiError catch (e) {
      error = e.message;
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<void> selectCompany(CompanyRef c) async {
    company = c;
    await prefs.setString(_kCompany, c.id);
    notifyListeners();
  }

  /// Back to the company list (switching without signing out).
  void leaveCompany() {
    company = null;
    prefs.remove(_kCompany);
    notifyListeners();
  }

  /// Role of the user in the active company (company role, else the organization role), as the server reports it.
  String? get roleInCompany => company == null ? organization?.organizationRole : (organization?.companyRoles[company!.id] ?? organization?.organizationRole);

  void clear() {
    organizations = const [];
    organization = null;
    companies = const [];
    company = null;
    api.organizationId = null;
    prefs.remove(_kOrg);
    prefs.remove(_kCompany);
    notifyListeners();
  }
}
