import 'package:file_picker/file_picker.dart';
import 'package:image_picker/image_picker.dart';

class PickedFile {
  const PickedFile(this.path, this.name);
  final String path;
  final String name;
}

/// Choosing files or photos on the phone. The system pickers do the work (Android file picker / iOS document picker / photo
/// library); the app never reads the library or storage on its own. On iOS the picker converts HEIC photos to JPEG.
abstract interface class FileImportService {
  Future<List<PickedFile>> pickFiles();
  Future<List<PickedFile>> pickPhotos();
}

class PlatformFileImport implements FileImportService {
  @override
  Future<List<PickedFile>> pickFiles() async {
    final r = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: const ['pdf', 'jpg', 'jpeg', 'png', 'tif', 'tiff', 'bmp', 'heic', 'heif', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt']);
    return [for (final f in r) if (f.path != null) PickedFile(f.path!, f.name)];
  }

  @override
  Future<List<PickedFile>> pickPhotos() async {
    final r = await ImagePicker().pickMultiImage();
    return [for (final x in r) PickedFile(x.path, x.name)];
  }
}
