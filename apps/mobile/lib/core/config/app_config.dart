/// Build-time configuration. Nothing secret lives here: the app talks to the Artify API with the signed-in user's own
/// session; it never carries database, storage, AI-provider or administrator credentials.
///
/// The server address is supplied at build time: `flutter build apk --dart-define=ARTIFY_API_URL=https://api.example.com/api/v1`.
class AppConfig {
  const AppConfig({required this.apiBaseUrl, this.appVersion = '0.1.0', this.platformName = 'mobile'});

  /// Base URL including the `/api/v1` prefix, without a trailing slash. Empty when the build has no server configured.
  final String apiBaseUrl;
  final String appVersion;
  final String platformName;

  bool get isConfigured => apiBaseUrl.isNotEmpty;

  static const AppConfig fromEnvironment = AppConfig(
    apiBaseUrl: String.fromEnvironment('ARTIFY_API_URL'),
    appVersion: String.fromEnvironment('ARTIFY_APP_VERSION', defaultValue: '0.1.0'),
  );

  Uri uri(String path, [Map<String, String>? query]) {
    final base = apiBaseUrl.endsWith('/') ? apiBaseUrl.substring(0, apiBaseUrl.length - 1) : apiBaseUrl;
    final u = Uri.parse('$base$path');
    return query == null || query.isEmpty ? u : u.replace(queryParameters: {...u.queryParameters, ...query});
  }
}
