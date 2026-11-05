import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/widgets/common.dart';
import '../workspace/metadata_validator.dart';
import '../workspace/workspace_models.dart';
import '../workspace/workspace_screens.dart';
import 'upload_models.dart';
import 'upload_queue.dart';

/// Where the document goes and what the company wants to know about it. Folders, categories and fields all come from the
/// company's own configuration; nothing here names a folder or a category.
class UploadFormScreen extends StatefulWidget {
  const UploadFormScreen({super.key, required this.services, required this.filePath, required this.fileName, this.source = UploadSource.file, this.pages, this.suggestedName});
  final AppServices services;
  final String filePath;
  final String fileName;
  final UploadSource source;
  final int? pages;
  final String? suggestedName;

  @override
  State<UploadFormScreen> createState() => _UploadFormScreenState();
}

class _UploadFormScreenState extends State<UploadFormScreen> {
  WsTarget? _target;
  String? _categoryId;
  final Map<String, Object?> _values = {};
  Map<String, String> _errors = {};
  late final TextEditingController _name = TextEditingController(text: widget.suggestedName ?? widget.fileName.replaceFirst(RegExp(r'\.[^.]+$'), ''));
  String? _problem;
  bool _busy = false;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit(UploadConfig cfg, {required bool asDraft}) async {
    final t = _target;
    if (t == null) {
      setState(() => _problem = 'Choose a folder first.');
      return;
    }
    final cats = cfg.categoriesFor(t.id);
    if (cats.isNotEmpty && _categoryId == null && !asDraft) {
      setState(() => _problem = 'Choose a document category.');
      return;
    }
    final fields = cfg.fieldsFor(t.id, _categoryId);
    final errs = asDraft ? <String, String>{} : MetadataValidator.validate(fields, _values);
    if (errs.isNotEmpty) {
      setState(() {
        _errors = errs;
        _problem = 'Please complete the highlighted fields.';
      });
      return;
    }
    final orgs = widget.services.orgs;
    setState(() {
      _busy = true;
      _problem = null;
    });
    try {
      final allowed = _categoryId == null ? null : cfg.categories.where((c) => c.id == _categoryId).firstOrNull?.allowedFileTypes;
      await widget.services.queue.enqueue(
        sourcePath: widget.filePath,
        organizationId: orgs.organization!.organizationId,
        companyId: orgs.company!.id,
        nodeId: t.id,
        categoryId: _categoryId,
        metadata: MetadataValidator.toPayload(fields, _values),
        displayName: _name.text,
        source: widget.source,
        pages: widget.pages,
        allowedTypes: allowed,
        asDraft: asDraft,
      );
      unawaited(widget.services.runner.kick());
      if (mounted) Navigator.of(context).pop(true);
    } on EnqueueRejected catch (e) {
      setState(() => _problem = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final size = File(widget.filePath).existsSync() ? File(widget.filePath).lengthSync() : 0;
    return Scaffold(
      appBar: AppBar(title: const Text('Send document')),
      body: SafeArea(
        child: WorkspaceLoader(
          services: widget.services,
          builder: (context, cfg, _) {
            final cats = _target == null ? <WsCategory>[] : cfg.categoriesFor(_target!.id);
            final fields = _target == null ? <WsField>[] : cfg.fieldsFor(_target!.id, _categoryId);
            return ListView(padding: const EdgeInsets.all(16), children: [
              Card(child: ListTile(leading: Icon(widget.source == UploadSource.scan ? Icons.document_scanner_outlined : Icons.description_outlined), title: Text(widget.fileName), subtitle: Text('${fmtBytes(size)}${widget.pages == null ? '' : ' · ${widget.pages} page${widget.pages == 1 ? '' : 's'}'}'))),
              const SizedBox(height: 12),
              Text('Company: ${widget.services.orgs.company!.name}', style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 12),
              if (_problem != null) ...[Banner2(_problem!), const SizedBox(height: 12)],
              TextField(controller: _name, decoration: const InputDecoration(labelText: 'Document name')),
              const SizedBox(height: 16),
              OutlinedButton.icon(
                icon: const Icon(Icons.folder_outlined),
                label: Align(alignment: Alignment.centerLeft, child: Text(_target == null ? 'Choose folder' : cfg.pathTo(_target!.id).map((t) => t.label).join(' › '))),
                onPressed: () async {
                  final t = await Navigator.of(context).push<WsTarget>(MaterialPageRoute(builder: (_) => WorkspaceBrowseScreen(services: widget.services, selecting: true)));
                  if (t != null) {
                    setState(() {
                      _target = t;
                      _categoryId = null;
                      _values
                        ..clear()
                        ..addAll(MetadataValidator.defaults(cfg.fieldsFor(t.id, null)));
                      _errors = {};
                    });
                  }
                },
              ),
              if (_target != null && _target!.description.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_target!.description, style: Theme.of(context).textTheme.bodySmall)),
              if (cats.isNotEmpty) ...[
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  key: ValueKey('cat-${_target?.id}'),
                  initialValue: _categoryId,
                  decoration: const InputDecoration(labelText: 'Document category'),
                  items: [for (final c in cats) DropdownMenuItem(value: c.id, child: Text(c.name))],
                  onChanged: (v) => setState(() {
                    _categoryId = v;
                    _values..clear()..addAll(MetadataValidator.defaults(cfg.fieldsFor(_target!.id, v)));
                    _errors = {};
                  }),
                ),
                if (_categoryId != null && cats.firstWhere((c) => c.id == _categoryId).description.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 6), child: Text(cats.firstWhere((c) => c.id == _categoryId).description, style: Theme.of(context).textTheme.bodySmall)),
              ],
              for (final f in fields) Padding(padding: const EdgeInsets.only(top: 16), child: MetadataFieldInput(field: f, cfg: cfg, value: _values[f.key], error: _errors[f.key], onChanged: (v) => setState(() {
                    _values[f.key] = v;
                    _errors.remove(f.key);
                  }))),
              const SizedBox(height: 24),
              FilledButton.icon(icon: const Icon(Icons.cloud_upload_outlined), onPressed: _busy ? null : () => _submit(cfg, asDraft: false), label: const Text('Send')),
              const SizedBox(height: 8),
              OutlinedButton(onPressed: _busy ? null : () => _submit(cfg, asDraft: true), child: const Text('Save as draft on this phone')),
              const SizedBox(height: 8),
              const Text('The document is stored on this phone first. It shows as “Verified in cloud storage” only after the server has confirmed it.', style: TextStyle(fontSize: 12)),
            ]);
          },
        ),
      ),
    );
  }
}

/// One input per field definition, chosen by its type.
class MetadataFieldInput extends StatelessWidget {
  const MetadataFieldInput({super.key, required this.field, required this.cfg, required this.value, required this.error, required this.onChanged});
  final WsField field;
  final UploadConfig cfg;
  final Object? value;
  final String? error;
  final ValueChanged<Object?> onChanged;

  String get _label => field.required ? '${field.label} *' : field.label;

  @override
  Widget build(BuildContext context) {
    final help = field.description.isEmpty ? null : field.description;
    switch (field.type) {
      case FieldType.boolean:
        return SwitchListTile(contentPadding: EdgeInsets.zero, title: Text(_label), subtitle: error != null ? Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)) : (help == null ? null : Text(help)), value: value == true, onChanged: onChanged);
      case FieldType.dropdown:
        return DropdownButtonFormField<String>(
          initialValue: field.options.any((o) => o.value == value) ? value as String? : null,
          decoration: InputDecoration(labelText: _label, helperText: help, errorText: error),
          items: [for (final o in field.options) DropdownMenuItem(value: o.value, child: Text(o.label))],
          onChanged: onChanged,
        );
      case FieldType.multiSelect:
        final chosen = ((value as List?) ?? const []).cast<String>();
        return InputDecorator(
          decoration: InputDecoration(labelText: _label, helperText: help, errorText: error),
          child: Wrap(spacing: 8, children: [for (final o in field.options) FilterChip(label: Text(o.label), selected: chosen.contains(o.value), onSelected: (on) => onChanged(on ? [...chosen, o.value] : chosen.where((x) => x != o.value).toList()))]),
        );
      case FieldType.date:
        return InkWell(
          onTap: () async {
            final d = await showDatePicker(context: context, initialDate: DateTime.tryParse('${value ?? ''}') ?? DateTime.now(), firstDate: DateTime(2000), lastDate: DateTime(2100));
            if (d != null) onChanged('${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}');
          },
          child: InputDecorator(decoration: InputDecoration(labelText: _label, helperText: help, errorText: error, suffixIcon: const Icon(Icons.calendar_today_outlined)), child: Text('${value ?? 'Choose a date'}')),
        );
      case FieldType.currency:
        final m = (value as Map?)?.cast<String, Object?>() ?? {};
        final allowed = (field.validation['allowed_currencies'] as List?)?.cast<String>() ?? const ['OMR', 'AED', 'SAR', 'USD', 'EUR'];
        return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(flex: 3, child: TextFormField(initialValue: '${m['amount'] ?? ''}', keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: InputDecoration(labelText: _label, helperText: help, errorText: error), onChanged: (v) => onChanged({...m, 'amount': v}))),
          const SizedBox(width: 8),
          Expanded(flex: 2, child: DropdownButtonFormField<String>(initialValue: allowed.contains(m['currency']) ? m['currency'] as String? : null, decoration: const InputDecoration(labelText: 'Currency'), items: [for (final c in allowed) DropdownMenuItem(value: c, child: Text(c))], onChanged: (v) => onChanged({...m, 'currency': v}))),
        ]);
      case FieldType.reference:
        final isNode = field.referenceTarget == 'workspace_node';
        final options = isNode ? [for (final t in cfg.targets) (t.id, cfg.pathTo(t.id).map((x) => x.label).join(' › '))] : [for (final c in cfg.categories) (c.id, c.name)];
        return DropdownButtonFormField<String>(initialValue: options.any((o) => o.$1 == value) ? value as String? : null, isExpanded: true, decoration: InputDecoration(labelText: _label, helperText: help, errorText: error), items: [for (final o in options) DropdownMenuItem(value: o.$1, child: Text(o.$2, overflow: TextOverflow.ellipsis))], onChanged: onChanged);
      case FieldType.number:
      case FieldType.decimal:
        return TextFormField(initialValue: '${value ?? ''}', keyboardType: TextInputType.numberWithOptions(decimal: field.type == FieldType.decimal), decoration: InputDecoration(labelText: _label, helperText: help, errorText: error), onChanged: onChanged);
      case FieldType.longText:
        return TextFormField(initialValue: '${value ?? ''}', minLines: 3, maxLines: 6, decoration: InputDecoration(labelText: _label, helperText: help, errorText: error), onChanged: onChanged);
      case FieldType.shortText:
      case FieldType.unknown:
        return TextFormField(initialValue: '${value ?? ''}', decoration: InputDecoration(labelText: _label, helperText: help, errorText: error), onChanged: onChanged);
    }
  }
}
