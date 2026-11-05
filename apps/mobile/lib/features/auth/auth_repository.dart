import 'dart:io' show Platform;

import '../../core/network/api_client.dart';
import '../../core/network/api_error.dart';
import '../../core/storage/secure_store.dart';
import 'auth_models.dart';

/// Authentication through the EXISTING backend (`/auth/*`, `/me`, `/devices`). There is no mobile-specific account system.
class AuthRepository {
  AuthRepository(this.api, this.tokens, {this.appVersion = '0.1.0'});
  final ApiClient api;
  final TokenStore tokens;
  final String appVersion;

  Future<LoginOutcome> login(String email, String password) async {
    final j = await api.postJson('/auth/login', body: {'email': email.trim(), 'password': password}, auth: false);
    return _outcome(j);
  }

  Future<LoginOutcome> verifyMfa(String mfaToken, String code) async {
    final j = await api.postJson('/auth/mfa/verify', body: {'mfa_token': mfaToken, 'code': code.trim()}, auth: false);
    return _outcome(j);
  }

  Future<LoginOutcome> _outcome(Map<String, dynamic> j) async {
    final tokensJson = j['tokens'];
    if (tokensJson is Map<String, dynamic>) {
      await tokens.save(Session.fromTokenJson(tokensJson));
      return const LoggedIn();
    }
    if (j['status'] == 'mfa_required' && j['mfa_token'] is String) return MfaRequired(j['mfa_token'] as String);
    if (j['mfa_enrollment_required'] == true) return const MfaEnrollmentRequired();
    throw const ApiError(status: 500, code: 'unexpected_response', message: 'The server answered in an unexpected way. Please try again.');
  }

  Future<Me> me({String? organizationId}) async {
    final prev = api.organizationId;
    api.organizationId = organizationId;
    try {
      return Me.fromJson(await api.getJson('/me'));
    } finally {
      api.organizationId = prev;
    }
  }

  Future<void> requestPasswordReset(String email) => api.postJson('/auth/password-reset/request', body: {'email': email.trim()}, auth: false);

  Future<void> confirmPasswordReset(String token, String newPassword) =>
      api.postJson('/auth/password-reset/confirm', body: {'token': token.trim(), 'new_password': newPassword}, auth: false);

  /// Ends the server session (and with it the refresh token). Local credentials are removed even when the server cannot
  /// be reached, so a lost connection never leaves a signed-in phone behind.
  Future<void> logout() async {
    final s = await tokens.load();
    try {
      if (s != null) await api.postJson('/auth/logout');
    } on ApiError {
      // the local session is removed below either way
    } finally {
      await tokens.clearSession();
    }
  }

  /// Registers this phone for the organization (device limits come from the subscription, not from this app).
  Future<String> ensureDevice(String organizationId) async {
    final known = await tokens.deviceId(organizationId);
    if (known != null) return known;
    final prev = api.organizationId;
    api.organizationId = organizationId;
    try {
      final j = await api.postJson('/devices', body: {
        'platform': Platform.isIOS ? 'ios' : 'android',
        'name': Platform.isIOS ? 'iPhone' : 'Android phone',
        'app_version': appVersion,
      });
      final id = j['id'] as String;
      await tokens.saveDeviceId(organizationId, id);
      return id;
    } finally {
      api.organizationId = prev;
    }
  }
}
