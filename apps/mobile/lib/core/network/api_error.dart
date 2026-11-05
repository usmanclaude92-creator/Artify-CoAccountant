/// A failed API call, reduced to what the interface may show. The text comes from the server's problem document or from
/// this file; it never contains tokens, request bodies or document content.
class ApiError implements Exception {
  const ApiError({required this.status, required this.code, required this.message, this.extra = const {}});

  /// HTTP status, or 0 when no answer arrived (offline, timeout, TLS failure).
  final int status;
  final String code;
  final String message;
  final Map<String, dynamic> extra;

  static const sessionExpiredCode = 'session_expired';
  static const networkCode = 'network_unavailable';

  factory ApiError.network([String? detail]) => ApiError(
        status: 0,
        code: networkCode,
        message: 'The server cannot be reached. Check your internet connection and try again.',
      );

  factory ApiError.sessionExpired() => const ApiError(
        status: 401,
        code: sessionExpiredCode,
        message: 'Your session has ended. Please sign in again.',
      );

  factory ApiError.fromProblem(int status, Map<String, dynamic>? body) {
    final code = (body?['code'] as String?) ?? _fallbackCode(status);
    final detail = (body?['detail'] as String?) ?? (body?['title'] as String?);
    final extra = <String, dynamic>{...?body}..removeWhere((k, _) => const {'type', 'title', 'status', 'detail', 'instance', 'code', 'correlation_id'}.contains(k));
    return ApiError(status: status, code: code, message: detail ?? _fallbackMessage(status), extra: extra);
  }

  static String _fallbackCode(int s) => switch (s) {
        401 => 'unauthenticated',
        403 => 'forbidden',
        404 => 'not_found',
        409 => 'conflict',
        422 => 'unprocessable',
        429 => 'rate_limited',
        _ => s >= 500 ? 'server_error' : 'bad_request',
      };

  static String _fallbackMessage(int s) => switch (s) {
        401 => 'Please sign in again.',
        403 => 'You do not have permission to do this.',
        404 => 'That item was not found, or you cannot access it.',
        409 => 'This conflicts with the current state. Refresh and try again.',
        429 => 'Too many attempts. Please wait a moment and try again.',
        _ => s >= 500 ? 'The server had a problem. Please try again later.' : 'The request could not be processed.',
      };

  bool get isNetwork => code == networkCode;
  bool get isSessionExpired => code == sessionExpiredCode;
  bool get isUnauthorized => status == 401;
  bool get isForbiddenOrHidden => status == 403 || status == 404;
  bool get isEntitlement => status == 402 || code == 'entitlement_exceeded';
  bool get isRateLimited => status == 429;
  bool get isServer => status >= 500;

  /// A failure that may succeed later without the user changing anything.
  bool get isRetryable => isNetwork || isServer || isRateLimited || status == 408;

  @override
  String toString() => 'ApiError($status, $code)';
}
