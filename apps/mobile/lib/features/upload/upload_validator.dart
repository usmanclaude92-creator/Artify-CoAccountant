import 'package:path/path.dart' as p;

class UploadCheck {
  const UploadCheck.ok() : problem = null;
  const UploadCheck.bad(this.problem);
  final String? problem;
  bool get isOk => problem == null;
}

/// A quick check on the phone so obvious mistakes are caught before a long upload. The server is the authority: it checks
/// the real content, the company's allowed types and the subscription's size limit, and its answer is always shown.
class UploadValidator {
  /// Types the Artify document engine accepts (kept in step with `file_safety.ALLOWED` on the server; a type missing here is
  /// still tried by the server only if the person picks it, never silently).
  static const accepted = {'pdf', 'jpg', 'jpeg', 'png', 'tif', 'tiff', 'bmp', 'heic', 'heif', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt'};

  static const _mime = {
    'pdf': 'application/pdf',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'tif': 'image/tiff',
    'tiff': 'image/tiff',
    'bmp': 'image/bmp',
    'heic': 'image/heic',
    'heif': 'image/heif',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'csv': 'text/csv',
    'txt': 'text/plain',
  };

  static String extensionOf(String name) => p.extension(name).replaceFirst('.', '').toLowerCase();
  static String mimeOf(String name) => _mime[extensionOf(name)] ?? 'application/octet-stream';

  /// [allowedForCategory] is the category's own list from the workspace configuration (null = any accepted type).
  static UploadCheck check(String fileName, int size, {int? maxBytes, List<String>? allowedForCategory}) {
    final ext = extensionOf(fileName);
    if (size <= 0) return const UploadCheck.bad('The file is empty.');
    if (ext.isEmpty || !accepted.contains(ext)) return UploadCheck.bad(ext.isEmpty ? 'The file has no type. Rename it with an extension such as .pdf or .jpg.' : 'Files of type .$ext are not accepted.');
    if (allowedForCategory != null && allowedForCategory.isNotEmpty && !allowedForCategory.map((e) => e.toLowerCase().replaceFirst('.', '')).contains(ext)) {
      return UploadCheck.bad('This category accepts only: ${allowedForCategory.join(', ')}.');
    }
    if (maxBytes != null && size > maxBytes) return UploadCheck.bad('The file is larger than the limit of ${(maxBytes / 1048576).toStringAsFixed(0)} MB.');
    return const UploadCheck.ok();
  }
}
