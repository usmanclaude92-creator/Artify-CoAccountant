import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:local_auth/local_auth.dart';

/// Device capabilities behind small interfaces so the logic that uses them can be tested without a device, and so that a
/// missing capability (no biometrics, no push provider) is an explicit, honest state.
abstract interface class ConnectivityService {
  Future<bool> get isOnline;
  Stream<bool> get changes;
}

class PlatformConnectivity implements ConnectivityService {
  PlatformConnectivity([Connectivity? c]) : _c = c ?? Connectivity();
  final Connectivity _c;
  static bool _online(List<ConnectivityResult> r) => r.any((x) => x != ConnectivityResult.none);

  @override
  Future<bool> get isOnline async => _online(await _c.checkConnectivity());
  @override
  Stream<bool> get changes => _c.onConnectivityChanged.map(_online).distinct();
}

abstract interface class BiometricService {
  /// Whether the device has an enrolled biometric or device credential the app may ask for.
  Future<bool> get available;
  Future<bool> authenticate(String reason);
}

class PlatformBiometrics implements BiometricService {
  PlatformBiometrics([LocalAuthentication? a]) : _a = a ?? LocalAuthentication();
  final LocalAuthentication _a;

  @override
  Future<bool> get available async {
    try {
      return await _a.isDeviceSupported() && (await _a.canCheckBiometrics || await _a.isDeviceSupported());
    } on Object {
      return false;
    }
  }

  @override
  Future<bool> authenticate(String reason) async {
    try {
      return await _a.authenticate(localizedReason: reason);
    } on Object {
      return false;
    }
  }
}

/// Push delivery is NOT implemented: no provider (FCM/APNs) has been chosen or configured, and the backend has no push-token
/// endpoint yet. This interface is where it plugs in; see docs/mobile/NOTIFICATIONS.md for the remaining setup.
abstract interface class PushRegistrar {
  bool get isAvailable;
  Future<void> register(String deviceId);
  Future<void> unregister();
}

class NoPushRegistrar implements PushRegistrar {
  @override
  bool get isAvailable => false;
  @override
  Future<void> register(String deviceId) async {}
  @override
  Future<void> unregister() async {}
}

class FakeConnectivity implements ConnectivityService {
  FakeConnectivity([this.online = true]);
  bool online;
  final _c = StreamController<bool>.broadcast();
  @override
  Future<bool> get isOnline async => online;
  @override
  Stream<bool> get changes => _c.stream;
  void set(bool v) {
    online = v;
    _c.add(v);
  }
}
