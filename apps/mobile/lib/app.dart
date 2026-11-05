import 'package:flutter/material.dart';

import 'app_services.dart';
import 'core/theme/app_theme.dart';
import 'core/widgets/common.dart';
import 'features/auth/auth_controller.dart';
import 'features/auth/auth_screens.dart';
import 'features/dashboard/home_screen.dart';
import 'features/documents/documents_screens.dart';
import 'features/notifications/notifications_screen.dart';
import 'features/orgs/org_screens.dart';
import 'features/settings/settings_screen.dart';

class ArtifyApp extends StatelessWidget {
  const ArtifyApp({super.key, required this.services});
  final AppServices services;

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Artify Co-Accountant',
        debugShowCheckedModeBanner: false,
        theme: artifyTheme(Brightness.light),
        darkTheme: artifyTheme(Brightness.dark),
        themeMode: ThemeMode.system,
        home: RootGate(services: services),
      );
}

/// Chooses what to show from the state alone: the server decides who is signed in and what they may open.
class RootGate extends StatefulWidget {
  const RootGate({super.key, required this.services});
  final AppServices services;
  @override
  State<RootGate> createState() => _RootGateState();
}

class _RootGateState extends State<RootGate> with WidgetsBindingObserver {
  bool _showLogin = false;
  AuthStatus? _last;

  AppServices get s => widget.services;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    s.auth.addListener(_onAuth);
    s.auth.start();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    s.auth.removeListener(_onAuth);
    super.dispose();
  }

  Future<void> _onAuth() async {
    final st = s.auth.status;
    if (st == _last) return;
    _last = st;
    if (st == AuthStatus.signedIn && s.auth.me != null) {
      s.orgs.setMemberships(s.auth.me!.memberships);
      if (s.orgs.organization != null) await s.orgs.selectOrganization(s.orgs.organization!);
      s.runner.resume();
      s.runner.start();
    } else if (st == AuthStatus.sessionExpired || st == AuthStatus.signedOut) {
      s.runner.pause(); // documents stay queued; they continue after the next sign-in
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused) s.auth.appBackgrounded();
    if (state == AppLifecycleState.resumed) {
      s.auth.appResumed();
      if (s.auth.status == AuthStatus.signedIn) s.runner.kick();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!s.config.isConfigured) {
      return const Scaffold(body: SafeArea(child: StateMessage(icon: Icons.settings_suggest_outlined, title: 'This build has no server address', body: 'Build the app with --dart-define=ARTIFY_API_URL=https://your-server/api/v1.')));
    }
    return ListenableBuilder(
      listenable: Listenable.merge([s.auth, s.orgs]),
      builder: (context, _) {
        switch (s.auth.status) {
          case AuthStatus.starting:
            return const Scaffold(body: Center(child: CircularProgressIndicator()));
          case AuthStatus.signedOut:
            return _showLogin ? LoginScreen(services: s) : WelcomeScreen(onContinue: () => setState(() => _showLogin = true), notice: s.auth.message);
          case AuthStatus.mfaRequired:
            return MfaScreen(services: s);
          case AuthStatus.sessionExpired:
            return SessionExpiredScreen(onSignIn: () {
              s.auth.acknowledgeExpiry();
              setState(() => _showLogin = true);
            });
          case AuthStatus.locked:
            return LockScreen(services: s);
          case AuthStatus.signedIn:
            if (s.orgs.organization == null) return OrgSelectScreen(services: s);
            if (s.orgs.company == null) return CompanySelectScreen(services: s);
            return HomeShell(key: ValueKey(s.orgs.company!.id), services: s);
        }
      },
    );
  }
}

class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.services});
  final AppServices services;
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final s = widget.services;
    final pages = [
      HomeScreen(services: s, openDocuments: () => setState(() => _tab = 1)),
      DocumentsScreen(services: s),
      NotificationsScreen(services: s),
      SettingsScreen(services: s),
    ];
    return Scaffold(
      body: IndexedStack(index: _tab, children: pages),
      bottomNavigationBar: ListenableBuilder(
        listenable: s.notices,
        builder: (context, _) => NavigationBar(
          selectedIndex: _tab,
          onDestinationSelected: (i) => setState(() => _tab = i),
          destinations: [
            const NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home), label: 'Home'),
            const NavigationDestination(icon: Icon(Icons.folder_copy_outlined), selectedIcon: Icon(Icons.folder_copy), label: 'Documents'),
            NavigationDestination(icon: Badge(isLabelVisible: s.notices.unread(s.orgs.company!.id) > 0, label: Text('${s.notices.unread(s.orgs.company!.id)}'), child: const Icon(Icons.notifications_outlined)), label: 'Notices'),
            const NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
          ],
        ),
      ),
    );
  }
}
