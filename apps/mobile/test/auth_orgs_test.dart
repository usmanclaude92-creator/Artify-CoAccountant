import 'package:artify_mobile/core/config/app_config.dart';
import 'package:artify_mobile/core/network/api_client.dart';
import 'package:artify_mobile/core/network/api_error.dart';
import 'package:artify_mobile/core/platform/platform_services.dart';
import 'package:artify_mobile/core/storage/secure_store.dart';
import 'package:artify_mobile/features/auth/auth_controller.dart';
import 'package:artify_mobile/features/auth/auth_repository.dart';
import 'package:artify_mobile/features/orgs/org_controller.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'support/fake_backend.dart';

class FakeBiometrics implements BiometricService {
  bool ok = true;
  @override
  Future<bool> get available async => true;
  @override
  Future<bool> authenticate(String reason) async => ok;
}

class Rig {
  Rig(this.backend, this.store, this.tokens, this.api, this.auth, this.bio, this.expired);
  final FakeBackend backend;
  final MemorySecureStore store;
  final TokenStore tokens;
  final ApiClient api;
  final AuthController auth;
  final FakeBiometrics bio;
  final List<int> expired;
}

Rig rig({FakeBackend? backend}) {
  final b = backend ?? FakeBackend();
  final store = MemorySecureStore();
  final tokens = TokenStore(store);
  final expired = <int>[];
  late final AuthController auth;
  final api = ApiClient(config: const AppConfig(apiBaseUrl: 'https://api.test/api/v1'), tokens: tokens, client: b.client, onSessionExpired: () {
    expired.add(1);
    auth.sessionEnded();
  });
  final bio = FakeBiometrics();
  auth = AuthController(repo: AuthRepository(api, tokens), tokens: tokens, api: api, biometrics: bio, lockAfter: Duration.zero);
  return Rig(b, store, tokens, api, auth, bio, expired);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('authentication', () {
    test('a correct login signs in, loads the profile from the server and keeps tokens only in secure storage', () async {
      SharedPreferences.setMockInitialValues({});
      final r = rig();
      await r.auth.login(' sara@example.com ', 'correct horse battery');
      expect(r.auth.status, AuthStatus.signedIn);
      expect(r.auth.me!.displayName, 'Sara Preparer');
      expect(r.store.data.keys, containsAll(['session.access', 'session.refresh']));
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getKeys().where((k) => prefs.get(k).toString().contains('access-1') || prefs.get(k).toString().contains('refresh-1')), isEmpty, reason: 'tokens never reach ordinary preferences');
      expect(r.backend.log, contains('POST /auth/login'));
    });

    test('a wrong password is refused with a plain message and nothing is stored', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'wrong');
      expect(r.auth.status, AuthStatus.starting);
      expect(r.auth.message, 'The email or password is not correct.');
      expect(r.store.data, isEmpty);
    });

    test('a second factor is asked for and only the right code signs in', () async {
      final r = rig(backend: FakeBackend()..requireMfa = true);
      await r.auth.login('sara@example.com', 'correct horse battery');
      expect(r.auth.status, AuthStatus.mfaRequired);
      await r.auth.verifyMfa('000000');
      expect(r.auth.status, AuthStatus.mfaRequired);
      expect(r.auth.message, isNotNull);
      await r.auth.verifyMfa('123456');
      expect(r.auth.status, AuthStatus.signedIn);
    });

    test('an account that must enrol a second factor is told where to do it, not signed in', () async {
      final r = rig(backend: FakeBackend()..enrollmentRequired = true);
      await r.auth.login('sara@example.com', 'correct horse battery');
      expect(r.auth.status, AuthStatus.signedOut);
      expect(r.auth.message, contains('second sign-in factor'));
      expect(r.store.data, isEmpty);
    });

    test('an expired access token is refreshed once and the call is repeated', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      r.backend.accessExpired = true;
      final me = await r.auth.repo.me();
      expect(me.email, 'sara@example.com');
      expect(r.backend.log.where((l) => l == 'POST /auth/refresh').length, 1);
      expect((await r.tokens.load())!.accessToken, 'access-2');
    });

    test('when refresh fails the session ends: credentials removed, callers told, state "session expired"', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      r.backend
        ..accessExpired = true
        ..refreshWorks = false;
      await expectLater(r.auth.repo.me(), throwsA(isA<ApiError>().having((e) => e.isSessionExpired, 'expired', isTrue)));
      expect(r.auth.status, AuthStatus.sessionExpired);
      expect(await r.tokens.load(), isNull);
      expect(r.expired, isNotEmpty);
    });

    test('two simultaneous calls share one refresh', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      r.backend.accessExpired = true;
      await Future.wait([r.auth.repo.me(), r.auth.repo.me()]);
      expect(r.backend.log.where((l) => l == 'POST /auth/refresh').length, lessThanOrEqualTo(1));
    });

    test('logout ends the server session and clears credentials even when the server cannot be reached', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      r.backend.logoutFails = true;
      await r.auth.logout();
      expect(r.auth.status, AuthStatus.signedOut);
      expect(await r.tokens.load(), isNull);
      expect(r.backend.log, contains('POST /auth/logout'));
    });

    test('a stored session is restored at start by asking the server; a dead one is expired', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      final again = AuthController(repo: r.auth.repo, tokens: r.tokens, api: r.api, biometrics: r.bio);
      await again.start();
      expect(again.status, AuthStatus.signedIn);
      r.backend
        ..accessExpired = true
        ..refreshWorks = false;
      final dead = AuthController(repo: r.auth.repo, tokens: r.tokens, api: r.api, biometrics: r.bio);
      await dead.start();
      expect(dead.status, AuthStatus.sessionExpired);
    });

    test('offline at start with a stored session stays usable instead of signing the user out', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      r.backend.online = false;
      final again = AuthController(repo: r.auth.repo, tokens: r.tokens, api: r.api, biometrics: r.bio);
      await again.start();
      expect(again.status, AuthStatus.signedIn);
    });

    test('the biometric lock protects an existing session and never replaces signing in', () async {
      final r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      await r.tokens.setBiometric(true);
      r.auth.appBackgrounded();
      await r.auth.appResumed();
      expect(r.auth.status, AuthStatus.locked);
      r.bio.ok = false;
      expect(await r.auth.unlock(), isFalse);
      expect(r.auth.status, AuthStatus.locked);
      r.bio.ok = true;
      expect(await r.auth.unlock(), isTrue);
      expect(r.auth.status, AuthStatus.signedIn);
      // without a session there is nothing to unlock: biometrics cannot sign anyone in
      await r.auth.logout();
      expect(r.auth.status, AuthStatus.signedOut);
    });

    test('password reset uses the existing endpoints', () async {
      final r = rig();
      await r.auth.repo.requestPasswordReset('sara@example.com');
      await r.auth.repo.confirmPasswordReset('token-from-email', 'a much longer new passphrase');
      expect(r.backend.log, containsAll(['POST /auth/password-reset/request', 'POST /auth/password-reset/confirm']));
    });

    test('unauthenticated calls never go out without a session', () async {
      final r = rig();
      await expectLater(r.api.getJson('/me'), throwsA(isA<ApiError>().having((e) => e.isSessionExpired, 'expired', isTrue)));
      expect(r.backend.log, isEmpty);
    });
  });

  group('organizations and companies', () {
    late Rig r;
    late SharedPreferences prefs;
    late OrgController orgs;
    final registered = <String>[];

    Future<void> signedIn(FakeBackend b) async {
      SharedPreferences.setMockInitialValues({});
      prefs = await SharedPreferences.getInstance();
      r = rig(backend: b);
      await r.auth.login('sara@example.com', 'correct horse battery');
      registered.clear();
      orgs = OrgController(api: r.api, prefs: prefs, onOrganizationSelected: (id) async => registered.add(id));
      orgs.setMemberships(r.auth.me!.memberships);
    }

    test('a user in one organization lands in it and sees only the companies the server lists', () async {
      await signedIn(FakeBackend());
      expect(orgs.organization!.organizationName, 'Gulf Accounting Partners');
      await orgs.selectOrganization(orgs.organization!);
      expect(orgs.companies.map((c) => c.name), ['Al Noor Trading', 'Gulf Builders']);
      expect(r.api.organizationId, 'org-1');
      expect(registered, ['org-1']);
    });

    test('several organizations: the user chooses, can switch without signing out, and each shows its own companies', () async {
      final b = FakeBackend();
      b.memberships.add({'organization_id': 'org-2', 'organization_name': 'Second Firm', 'organization_status': 'active', 'organization_role': null, 'company_roles': {'co-z': 'company_employee'}});
      b.companiesByOrg['org-2'] = [
        {'id': 'co-z', 'name': 'Zed Stores', 'code': 'ZS', 'status': 'active', 'country': 'AE', 'base_currency': 'AED', 'legal_name': null, 'assigned_to_me': false},
      ];
      await signedIn(b);
      expect(orgs.organization, isNull, reason: 'two organizations: the user decides');
      await orgs.selectOrganization(orgs.organizations[1]);
      expect(orgs.companies.map((c) => c.name), ['Zed Stores']);
      await orgs.selectCompany(orgs.companies.single);
      expect(orgs.roleInCompany, 'company_employee');
      await orgs.selectOrganization(orgs.organizations[0]);
      expect(orgs.companies.map((c) => c.id), ['co-a', 'co-b']);
      expect(r.auth.status, AuthStatus.signedIn);
      expect(r.api.organizationId, 'org-1');
    });

    test('switching between companies keeps the session and remembers the choice', () async {
      await signedIn(FakeBackend());
      await orgs.selectOrganization(orgs.organization!);
      await orgs.selectCompany(orgs.companies[0]);
      expect(orgs.company!.id, 'co-a');
      await orgs.selectCompany(orgs.companies[1]);
      expect(orgs.company!.id, 'co-b');
      expect(prefs.getString('sel.company'), 'co-b');
      orgs.leaveCompany();
      expect(orgs.company, isNull);
    });

    test('many companies are paged in full and archived ones are hidden', () async {
      final b = FakeBackend();
      b.companiesByOrg['org-1'] = [for (var i = 0; i < 230; i++) {'id': 'c$i', 'name': 'Company $i', 'code': 'C$i', 'status': i == 5 ? 'archived' : 'active', 'country': 'OM', 'base_currency': 'OMR', 'legal_name': null, 'assigned_to_me': false}];
      await signedIn(b);
      await orgs.selectOrganization(orgs.organization!);
      expect(orgs.companies.length, 229);
      expect(orgs.companies.any((c) => c.id == 'c5'), isFalse);
    });

    test('an organization the user does not belong to answers "not found" and shows no companies', () async {
      final b = FakeBackend();
      await signedIn(b);
      b.companiesByOrg.remove('org-1');
      await orgs.selectOrganization(orgs.organization!);
      expect(orgs.companies, isEmpty);
      expect(orgs.error, isNotNull);
    });

    test('a device limit reached on the server is explained and points to the administrator', () async {
      SharedPreferences.setMockInitialValues({});
      prefs = await SharedPreferences.getInstance();
      r = rig();
      await r.auth.login('sara@example.com', 'correct horse battery');
      final o = OrgController(api: r.api, prefs: prefs, onOrganizationSelected: (_) async => throw const ApiError(status: 402, code: 'entitlement_exceeded', message: 'Limit limit.devices reached (5 of 5 in use).'));
      o.setMemberships(r.auth.me!.memberships);
      await o.selectOrganization(o.organization!);
      expect(o.deviceNotice, contains('Ask the administrator'));
    });

    test('the active organization is sent to the server on every call, and clearing it removes it', () async {
      await signedIn(FakeBackend());
      await orgs.selectOrganization(orgs.organization!);
      await r.api.getJson('/me');
      expect(r.backend.headersSeen.last['X-Organization-Id'], 'org-1');
      orgs.clear();
      await r.api.getJson('/me');
      expect(r.backend.headersSeen.last.containsKey('X-Organization-Id'), isFalse);
    });
  });
}
