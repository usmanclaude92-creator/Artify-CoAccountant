import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../core/platform/platform_services.dart';
import 'upload_models.dart';
import 'upload_queue.dart';
import 'upload_service.dart';

/// Drains the queue one document at a time while the phone is online, and again when connectivity returns, the app comes
/// back to the foreground, or a retry delay passes. It never marks anything as uploaded itself; that is the service's job
/// and only on the server's confirmation.
class QueueRunner extends ChangeNotifier {
  QueueRunner({required this.queue, required this.service, required this.connectivity, this.autoSchedule = true});
  final UploadQueue queue;
  final UploadService service;
  final ConnectivityService connectivity;
  final bool autoSchedule;

  StreamSubscription<bool>? _sub;
  Timer? _timer;
  bool _running = false;
  bool paused = false;

  bool get isRunning => _running;

  Future<void> start() async {
    await queue.load();
    _sub ??= connectivity.changes.listen((online) {
      if (online) unawaited(kick());
      notifyListeners();
    });
    await kick();
  }

  /// Sign-out or an expired session: stop sending until the next [resume].
  void pause() {
    paused = true;
    _timer?.cancel();
    notifyListeners();
  }

  Future<void> resume() async {
    paused = false;
    await kick();
  }

  Future<void> kick() async {
    if (_running || paused) return;
    _running = true;
    notifyListeners();
    try {
      while (!paused && await connectivity.isOnline) {
        final now = DateTime.now().toUtc();
        final next = queue.items.where((i) => i.state == UploadState.waiting && (i.nextAttemptAt == null || !i.nextAttemptAt!.isAfter(now))).firstOrNull;
        if (next == null) break;
        await service.run(next);
        if (next.state == UploadState.waiting && next.errorCode == 'session_expired') {
          paused = true; // nothing can be sent until the person signs in again
          break;
        }
      }
    } finally {
      _running = false;
      _scheduleRetry();
      notifyListeners();
    }
  }

  void _scheduleRetry() {
    _timer?.cancel();
    if (!autoSchedule || paused) return;
    final waits = queue.items.where((i) => i.state == UploadState.waiting && i.nextAttemptAt != null).map((i) => i.nextAttemptAt!).toList()..sort();
    if (waits.isEmpty) return;
    final delay = waits.first.difference(DateTime.now().toUtc());
    _timer = Timer(delay.isNegative ? const Duration(seconds: 1) : delay, () => unawaited(kick()));
  }

  @override
  void dispose() {
    _sub?.cancel();
    _timer?.cancel();
    super.dispose();
  }
}
