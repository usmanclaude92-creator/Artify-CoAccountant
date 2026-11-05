import 'dart:io';

import 'package:artify_mobile/app_services.dart';
import 'package:artify_mobile/core/config/app_config.dart';
import 'package:artify_mobile/core/network/api_client.dart';
import 'package:artify_mobile/core/platform/file_import.dart';
import 'package:artify_mobile/core/platform/platform_services.dart';
import 'package:artify_mobile/core/storage/secure_store.dart';
import 'package:artify_mobile/features/auth/auth_controller.dart';
import 'package:artify_mobile/features/auth/auth_repository.dart';
import 'package:artify_mobile/features/documents/documents_repository.dart';
import 'package:artify_mobile/features/notifications/notification_center.dart';
import 'package:artify_mobile/features/orgs/org_controller.dart';
import 'package:artify_mobile/features/upload/queue_runner.dart';
import 'package:artify_mobile/features/upload/upload_queue.dart';
import 'package:artify_mobile/features/upload/upload_service.dart';
import 'package:artify_mobile/features/workspace/workspace_repository.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fake_backend.dart';

class FakeBio implements BiometricService {
  FakeBio({this.hasBiometrics = true});
  bool hasBiometrics;
  bool ok = true;
  @override
  Future<bool> get available async => hasBiometrics;
  @override
  Future<bool> authenticate(String reason) async => ok;
}

class FakeImport implements FileImportService {
  List<PickedFile> files = [];
  List<PickedFile> photos = [];
  @override
  Future<List<PickedFile>> pickFiles() async => files;
  @override
  Future<List<PickedFile>> pickPhotos() async => photos;
}

class Harness {
  Harness(this.services, this.backend, this.store, this.net, this.bio, this.imports, this.root);
  final AppServices services;
  final FakeBackend backend;
  final MemorySecureStore store;
  final FakeConnectivity net;
  final FakeBio bio;
  final FakeImport imports;
  final Directory root;
}

/// A complete app wired to the fake backend: real controllers and repositories, fake network and device services.
Future<Harness> harness({FakeBackend? backend, bool hasBiometrics = true}) async {
  SharedPreferences.setMockInitialValues({});
  final prefs = await SharedPreferences.getInstance();
  final b = backend ?? FakeBackend();
  final root = await Directory.systemTemp.createTemp('app');
  final store = MemorySecureStore();
  final tokens = TokenStore(store);
  const config = AppConfig(apiBaseUrl: 'https://api.test/api/v1', appVersion: '9.9.9');
  late final AuthController auth;
  final api = ApiClient(config: config, tokens: tokens, client: b.client, onSessionExpired: () => auth.sessionEnded());
  final bio = FakeBio(hasBiometrics: hasBiometrics);
  final repo = AuthRepository(api, tokens, appVersion: config.appVersion);
  auth = AuthController(repo: repo, tokens: tokens, api: api, biometrics: bio, lockAfter: Duration.zero);
  final orgs = OrgController(api: api, prefs: prefs, onOrganizationSelected: (id) async => await repo.ensureDevice(id));
  final queue = UploadQueue(Directory('${root.path}/queue'));
  final net = FakeConnectivity(true);
  final runner = QueueRunner(queue: queue, service: UploadService(api, queue), connectivity: net, autoSchedule: false);
  final docs = DocumentsRepository(api);
  final imports = FakeImport();
  final services = AppServices(
    config: config,
    tokens: tokens,
    api: api,
    auth: auth,
    orgs: orgs,
    workspace: WorkspaceRepository(api, Directory('${root.path}/workspace')),
    queue: queue,
    runner: runner,
    docs: docs,
    notices: NotificationCenter(queue: queue, docs: docs, prefs: prefs),
    connectivity: net,
    biometrics: bio,
    push: NoPushRegistrar(),
    imports: imports,
    prefs: prefs,
    scanDir: Directory('${root.path}/scans'),
    cacheDir: Directory('${root.path}/cache'),
  );
  return Harness(services, b, store, net, bio, imports, root);
}
