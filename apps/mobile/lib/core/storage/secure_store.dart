import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// The only place credentials may be kept: Android Keystore-backed encrypted storage and the iOS Keychain (through the
/// vetted `flutter_secure_storage` package). Never use shared preferences or plain files for tokens.
abstract interface class SecureStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
  Future<void> deleteAll();
}

class PlatformSecureStore implements SecureStore {
  PlatformSecureStore([FlutterSecureStorage? storage])
      : _s = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(),
              iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
            );
  final FlutterSecureStorage _s;

  @override
  Future<String?> read(String key) => _s.read(key: key);
  @override
  Future<void> write(String key, String value) => _s.write(key: key, value: value);
  @override
  Future<void> delete(String key) => _s.delete(key: key);
  @override
  Future<void> deleteAll() => _s.deleteAll();
}

/// Tests and previews only.
class MemorySecureStore implements SecureStore {
  final Map<String, String> data = {};
  @override
  Future<String?> read(String key) async => data[key];
  @override
  Future<void> write(String key, String value) async => data[key] = value;
  @override
  Future<void> delete(String key) async => data.remove(key);
  @override
  Future<void> deleteAll() async => data.clear();
}

class Session {
  const Session({required this.accessToken, required this.refreshToken, required this.expiresAt});
  final String accessToken;
  final String refreshToken;
  final DateTime expiresAt;

  /// From the server's `TokenResponse` (`access_token`, `refresh_token`, `expires_in` seconds).
  factory Session.fromTokenJson(Map<String, dynamic> j, {DateTime? now}) => Session(
        accessToken: j['access_token'] as String,
        refreshToken: j['refresh_token'] as String,
        expiresAt: (now ?? DateTime.now().toUtc()).add(Duration(seconds: (j['expires_in'] as num?)?.toInt() ?? 600)),
      );
}

/// Tokens and the device id, kept in [SecureStore] only.
class TokenStore {
  TokenStore(this._store);
  final SecureStore _store;

  static const _access = 'session.access';
  static const _refresh = 'session.refresh';
  static const _expires = 'session.expires';
  static const _deviceId = 'device.id';
  static const _biometric = 'lock.biometric';

  Future<Session?> load() async {
    final a = await _store.read(_access);
    final r = await _store.read(_refresh);
    if (a == null || r == null) return null;
    final e = DateTime.tryParse(await _store.read(_expires) ?? '') ?? DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
    return Session(accessToken: a, refreshToken: r, expiresAt: e);
  }

  Future<void> save(Session s) async {
    await _store.write(_access, s.accessToken);
    await _store.write(_refresh, s.refreshToken);
    await _store.write(_expires, s.expiresAt.toUtc().toIso8601String());
  }

  Future<void> clearSession() async {
    await _store.delete(_access);
    await _store.delete(_refresh);
    await _store.delete(_expires);
  }

  Future<String?> deviceId(String orgId) => _store.read('$_deviceId.$orgId');
  Future<void> saveDeviceId(String orgId, String id) => _store.write('$_deviceId.$orgId', id);

  Future<bool> biometricEnabled() async => (await _store.read(_biometric)) == '1';
  Future<void> setBiometric(bool on) => on ? _store.write(_biometric, '1') : _store.delete(_biometric);

  Future<void> wipe() => _store.deleteAll();
}
