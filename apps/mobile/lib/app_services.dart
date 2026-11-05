import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'core/config/app_config.dart';
import 'core/network/api_client.dart';
import 'core/platform/file_import.dart';
import 'core/platform/platform_services.dart';
import 'core/storage/secure_store.dart';
import 'features/auth/auth_controller.dart';
import 'features/auth/auth_repository.dart';
import 'features/documents/documents_repository.dart';
import 'features/notifications/notification_center.dart';
import 'features/orgs/org_controller.dart';
import 'features/upload/queue_runner.dart';
import 'features/upload/upload_queue.dart';
import 'features/upload/upload_service.dart';
import 'features/workspace/workspace_repository.dart';

/// Everything the screens share, created once. Tests build one with fakes.
class AppServices {
  AppServices({
    required this.config,
    required this.tokens,
    required this.api,
    required this.auth,
    required this.orgs,
    required this.workspace,
    required this.queue,
    required this.runner,
    required this.docs,
    required this.notices,
    required this.connectivity,
    required this.biometrics,
    required this.push,
    required this.imports,
    required this.prefs,
    required this.scanDir,
    required this.cacheDir,
  });

  final AppConfig config;
  final TokenStore tokens;
  final ApiClient api;
  final AuthController auth;
  final OrgController orgs;
  final WorkspaceRepository workspace;
  final UploadQueue queue;
  final QueueRunner runner;
  final DocumentsRepository docs;
  final NotificationCenter notices;
  final ConnectivityService connectivity;
  final BiometricService biometrics;
  final PushRegistrar push;
  final FileImportService imports;
  final SharedPreferences prefs;
  final Directory scanDir;
  final Directory cacheDir;

  static Future<AppServices> create(AppConfig config) async {
    final prefs = await SharedPreferences.getInstance();
    final support = await getApplicationSupportDirectory(); // private to this app
    final tokens = TokenStore(PlatformSecureStore());
    final connectivity = PlatformConnectivity();
    final biometrics = PlatformBiometrics();
    late final AuthController auth;
    final api = ApiClient(config: config, tokens: tokens, onSessionExpired: () => auth.sessionEnded());
    final repo = AuthRepository(api, tokens, appVersion: config.appVersion);
    auth = AuthController(repo: repo, tokens: tokens, api: api, biometrics: biometrics);
    final orgs = OrgController(api: api, prefs: prefs, onOrganizationSelected: (id) async {
      await repo.ensureDevice(id);
    });
    final queue = UploadQueue(Directory('${support.path}/queue'));
    final runner = QueueRunner(queue: queue, service: UploadService(api, queue), connectivity: connectivity);
    final docs = DocumentsRepository(api);
    return AppServices(
      config: config,
      tokens: tokens,
      api: api,
      auth: auth,
      orgs: orgs,
      workspace: WorkspaceRepository(api, Directory('${support.path}/workspace')),
      queue: queue,
      runner: runner,
      docs: docs,
      notices: NotificationCenter(queue: queue, docs: docs, prefs: prefs),
      connectivity: connectivity,
      biometrics: biometrics,
      push: NoPushRegistrar(),
      imports: PlatformFileImport(),
      prefs: prefs,
      scanDir: Directory('${support.path}/scans'),
      cacheDir: Directory('${support.path}/cache'),
    );
  }

  /// Sign out / clear data: nothing of the user's documents or workspace stays on the phone.
  Future<void> wipeLocalData() async {
    runner.pause();
    await queue.wipe();
    await workspace.clear();
    if (await scanDir.exists()) await scanDir.delete(recursive: true);
    if (await cacheDir.exists()) await cacheDir.delete(recursive: true);
    orgs.clear();
  }
}
