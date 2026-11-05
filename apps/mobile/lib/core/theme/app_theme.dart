import 'package:flutter/material.dart';

/// The Artify brand as it is already used by the web and Windows apps (packages/ui tokens): a deep teal accent on a
/// quiet neutral surface, system sans-serif type, 8 px corners, text-plus-colour status. Light and dark follow the system.
class ArtifyColors {
  static const accentLight = Color(0xFF0B6B5F);
  static const accentDark = Color(0xFF3FB8A6);
  static const danger = Color(0xFFA4262C);
  static const success = Color(0xFF1D6B3A);
  static const warning = Color(0xFF8A5A00);
  static const info = Color(0xFF1F4F99);
}

ThemeData artifyTheme(Brightness b) {
  final dark = b == Brightness.dark;
  final accent = dark ? ArtifyColors.accentDark : ArtifyColors.accentLight;
  final scheme = ColorScheme.fromSeed(seedColor: ArtifyColors.accentLight, brightness: b).copyWith(
    primary: accent,
    onPrimary: dark ? const Color(0xFF07201C) : Colors.white,
    surface: dark ? const Color(0xFF171C23) : Colors.white,
    onSurface: dark ? const Color(0xFFE8ECF2) : const Color(0xFF14181F),
    error: dark ? const Color(0xFFFF8A8F) : ArtifyColors.danger,
    outline: dark ? const Color(0xFF2F3947) : const Color(0xFFCFD5DF),
  );
  const radius = BorderRadius.all(Radius.circular(8));
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: dark ? const Color(0xFF0F1318) : const Color(0xFFF6F7F9),
    appBarTheme: AppBarTheme(backgroundColor: scheme.surface, foregroundColor: scheme.onSurface, elevation: 0, scrolledUnderElevation: 1),
    cardTheme: CardThemeData(color: scheme.surface, elevation: 0, margin: EdgeInsets.zero, shape: RoundedRectangleBorder(borderRadius: radius, side: BorderSide(color: scheme.outline))),
    inputDecorationTheme: InputDecorationTheme(border: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide(color: scheme.outline)), isDense: false),
    filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(minimumSize: const Size(48, 48), shape: const RoundedRectangleBorder(borderRadius: radius))),
    outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(minimumSize: const Size(48, 48), shape: const RoundedRectangleBorder(borderRadius: radius))),
    navigationBarTheme: NavigationBarThemeData(backgroundColor: scheme.surface, indicatorColor: accent.withValues(alpha: 0.16)),
  );
}

/// Status is always shown as text and an icon as well as colour.
enum Tone { neutral, success, warning, danger, info }

Color toneColor(BuildContext c, Tone t) {
  final dark = Theme.of(c).brightness == Brightness.dark;
  return switch (t) {
    Tone.success => dark ? const Color(0xFF7FD49B) : ArtifyColors.success,
    Tone.warning => dark ? const Color(0xFFF0C36D) : ArtifyColors.warning,
    Tone.danger => dark ? const Color(0xFFFF8A8F) : ArtifyColors.danger,
    Tone.info => dark ? const Color(0xFF8DB6F5) : ArtifyColors.info,
    Tone.neutral => Theme.of(c).colorScheme.onSurface.withValues(alpha: 0.7),
  };
}
