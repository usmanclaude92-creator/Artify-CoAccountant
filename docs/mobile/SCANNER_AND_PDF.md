# Scanner and PDF

All image work is Dart (package `image`), runs in a background isolate and is covered by tests on synthetic photos; the camera preview itself uses the `camera` plugin and can only be exercised on a device.

* **Live preview, flash (off/auto/on/torch), front/back camera:** `scanner_screen.dart` (device only; untested here).
* **Page detection:** grey-scale → blur → Otsu threshold → largest connected region (light page on dark surface, then the reverse) → four extreme corners; accepted only if the quadrilateral explains the region (`edge_detector.dart`). Returns *nothing* when unsure; the app then proposes the whole picture and the person drags the four corners. Detection runs on the captured still, **not** continuously on the live preview (no live outline).
* **Perspective correction:** homography solved from the four corners, bilinear sampling (`perspective.dart`); output size follows the page's own edge lengths (long edge ≤ 2 200 px standard, 3 000 high).
* **Clean-up:** colour / grey / black-and-white (adaptive local threshold), brightness, contrast, shadow reduction (division by the blurred local paper colour) (`enhance.dart`).
* **Pages:** the original photo is kept; crop, rotation and look are settings applied at render time. Add, retake (same position), delete, rotate, reorder, adjust, apply a mode to all pages; drafts survive restarting the app (`scan_session.dart`).
* **PDF:** one file, pages in order, each shaped like its picture at A4 width with no margin, JPEG 85 (standard) or 92 (high) (`pdf_builder.dart`). The preview shows the pages as embedded plus the file size; it is not a PDF renderer.
* **HEIC/HEIF:** the Dart image library cannot decode them; the iOS picker converts photos to JPEG; a HEIC *file* chosen from storage is uploaded as is (the server accepts `.heic`) but not scanned.
* **Limits (honest):** no ML model; white page on white desk, cluttered backgrounds or a page filling the frame fall back to manual corners; very large photos are processed in an isolate but may be slow on low-end phones (not measured).
