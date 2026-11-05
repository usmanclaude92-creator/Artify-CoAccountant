import 'package:flutter/foundation.dart';

import '../../core/network/api_client.dart';
import '../../core/network/api_error.dart';
import '../../core/platform/platform_services.dart';
import '../../core/storage/secure_store.dart';
import 'auth_models.dart';
import 'auth_repository.dart';

enum AuthStatus { starting, signedOut, mfaRequired, signedIn, sessionExpired, locked }

/// Owns who is signed in. The screens only read this; every decision (valid password, MFA, expiry) is the server's.
class AuthController extends ChangeNotifier {
  AuthController({required this.repo, required this.tokens, required this.api, required this.biometrics, this.lockAfter = const Duration(seconds: 30)});

  final AuthRepository repo;
  final TokenStore tokens;
  final ApiClient api;
  final BiometricService biometrics;
  final Duration lockAfter;

  AuthStatus status = AuthStatus.starting;
  Me? me;
  String? message;
  String? _mfaToken;
  DateTime? _backgroundedAt;
  bool busy = false;

  /// Called once at start: restores a stored session by asking the server who it belongs to.
  Future<void> start() async {
    final s = await tokens.load();
    if (s == null) return _set(AuthStatus.signedOut);
    try {
      me = await repo.me();
      _set(await tokens.biometricEnabled() ? AuthStatus.locked : AuthStatus.signedIn);
    } on ApiError catch (e) {
      if (e.isSessionExpired || e.isUnauthorized) {
        _expire();
      } else if (e.isNetwork) {
        // Offline start with a stored session: stay signed in without a profile; screens that need the server say so.
        _set(await tokens.biometricEnabled() ? AuthStatus.locked : AuthStatus.signedIn);
      } else {
        _set(AuthStatus.signedOut, e.message);
      }
    }
  }

  Future<void> login(String email, String password) => _guard(() async => _apply(await repo.login(email, password)));

  Future<void> verifyMfa(String code) => _guard(() async {
        final t = _mfaToken;
        if (t == null) return _set(AuthStatus.signedOut);
        await _apply(await repo.verifyMfa(t, code));
      });

  Future<void> _apply(LoginOutcome o) async {
    switch (o) {
      case LoggedIn():
        me = await repo.me();
        _set(AuthStatus.signedIn);
      case MfaRequired(:final mfaToken):
        _mfaToken = mfaToken;
        _set(AuthStatus.mfaRequired);
      case MfaEnrollmentRequired():
        _set(AuthStatus.signedOut, 'This account needs a second sign-in factor before it can be used. Set it up on the Artify web app or the Windows app, then sign in here.');
    }
  }

  Future<void> logout() async {
    await repo.logout();
    me = null;
    _mfaToken = null;
    _set(AuthStatus.signedOut);
  }

  /// The API client reports that refresh failed: the session is over.
  void sessionEnded() => _expire();

  void _expire() {
    me = null;
    _set(AuthStatus.sessionExpired, 'Your session has ended. Please sign in again.');
  }

  void acknowledgeExpiry() => _set(AuthStatus.signedOut);

  // ---- biometric lock: protects an existing session; it never replaces signing in
  void appBackgrounded() => _backgroundedAt = DateTime.now();

  Future<void> appResumed() async {
    final at = _backgroundedAt;
    _backgroundedAt = null;
    if (status == AuthStatus.signedIn && at != null && DateTime.now().difference(at) >= lockAfter && await tokens.biometricEnabled()) {
      _set(AuthStatus.locked);
    }
  }

  Future<bool> unlock() async {
    final ok = await biometrics.authenticate('Unlock Artify Co-Accountant');
    if (ok) {
      _set(AuthStatus.signedIn);
    } else {
      message = 'Could not verify it is you. Try again, or sign out.';
      notifyListeners();
    }
    return ok;
  }

  Future<void> _guard(Future<void> Function() f) async {
    busy = true;
    message = null;
    notifyListeners();
    try {
      await f();
    } on ApiError catch (e) {
      message = switch (e.code) {
        'invalid_credentials' || 'unauthenticated' => 'The email or password is not correct.',
        _ => e.message,
      };
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  void _set(AuthStatus s, [String? msg]) {
    status = s;
    message = msg;
    notifyListeners();
  }
}
