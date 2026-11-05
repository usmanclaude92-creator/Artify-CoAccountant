import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Text plus colour (never colour alone) for a status.
class StatusPill extends StatelessWidget {
  const StatusPill(this.label, {super.key, this.tone = Tone.neutral, this.icon});
  final String label;
  final Tone tone;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final c = toneColor(context, tone);
    return Semantics(
      label: 'Status: $label',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(color: c.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(999), border: Border.all(color: c.withValues(alpha: 0.5))),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: 14, color: c), const SizedBox(width: 4)],
          Flexible(child: Text(label, style: Theme.of(context).textTheme.labelMedium?.copyWith(color: c, fontWeight: FontWeight.w600), overflow: TextOverflow.ellipsis)),
        ]),
      ),
    );
  }
}

class StateMessage extends StatelessWidget {
  const StateMessage({super.key, required this.icon, required this.title, this.body, this.action});
  final IconData icon;
  final String title;
  final String? body;
  final Widget? action;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 48, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 12),
            Text(title, style: Theme.of(context).textTheme.titleMedium, textAlign: TextAlign.center),
            if (body != null) ...[const SizedBox(height: 8), Text(body!, textAlign: TextAlign.center)],
            if (action != null) ...[const SizedBox(height: 16), action!],
          ]),
        ),
      );
}

class Banner2 extends StatelessWidget {
  const Banner2(this.message, {super.key, this.tone = Tone.danger});
  final String message;
  final Tone tone;
  @override
  Widget build(BuildContext context) {
    final c = toneColor(context, tone);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.1), borderRadius: BorderRadius.circular(8), border: Border.all(color: c.withValues(alpha: 0.5))),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(tone == Tone.danger ? Icons.error_outline : Icons.info_outline, color: c, size: 20),
        const SizedBox(width: 8),
        Expanded(child: Text(message)),
      ]),
    );
  }
}

String fmtBytes(int n) => n >= 1048576 ? '${(n / 1048576).toStringAsFixed(1)} MB' : n >= 1024 ? '${(n / 1024).toStringAsFixed(0)} KB' : '$n B';

String fmtWhen(DateTime t) {
  final l = t.toLocal();
  String two(int v) => v.toString().padLeft(2, '0');
  return '${l.year}-${two(l.month)}-${two(l.day)} ${two(l.hour)}:${two(l.minute)}';
}
