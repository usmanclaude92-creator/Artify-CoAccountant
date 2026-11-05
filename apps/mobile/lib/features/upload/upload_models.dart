enum UploadState { draft, waiting, uploading, verifying, completed, failed, cancelled, quarantined }

enum UploadSource { scan, file, gallery }

/// One document on its way to the cloud. The local file is the person's original; the record carries everything needed to
/// repeat the upload safely (the idempotency key makes a repeat return the first result instead of creating a duplicate).
class QueueItem {
  QueueItem({
    required this.id,
    required this.organizationId,
    required this.companyId,
    required this.fileName,
    required this.displayName,
    required this.mime,
    required this.size,
    required this.sha256,
    required this.localPath,
    required this.nodeId,
    required this.categoryId,
    required this.metadata,
    required this.idempotencyKey,
    required this.source,
    required this.createdAt,
    this.pages,
    this.state = UploadState.waiting,
    this.attempts = 0,
    this.progress = 0,
    this.errorCode,
    this.errorMessage,
    this.retryable = true,
    this.serverOperationId,
    this.documentId,
    this.allowCopy = false,
    this.nextAttemptAt,
    DateTime? updatedAt,
  }) : updatedAt = updatedAt ?? createdAt;

  final String id;
  final String organizationId;
  final String companyId;
  final String fileName;
  final String displayName;
  final String mime;
  final int size;
  final String sha256;
  String localPath;
  final String nodeId;
  final String? categoryId;
  final Map<String, Object?> metadata;
  final String idempotencyKey;
  final UploadSource source;
  final DateTime createdAt;
  final int? pages;

  UploadState state;
  int attempts;
  double progress;
  String? errorCode;
  String? errorMessage;
  bool retryable;
  String? serverOperationId;
  String? documentId;
  bool allowCopy;
  DateTime? nextAttemptAt;
  DateTime updatedAt;

  bool get isFinished => state == UploadState.completed || state == UploadState.cancelled;
  bool get isActive => state == UploadState.waiting || state == UploadState.uploading || state == UploadState.verifying;

  /// What the person is told. "Verified in cloud storage" appears only after the server confirmed storage; before that the
  /// document is only ever "saved on this device" or "waiting".
  String stageLabel({required bool online}) => switch (state) {
        UploadState.draft => 'Draft — saved on this device',
        UploadState.waiting => online ? 'Waiting to upload' : 'Saved on this device — waiting for a connection',
        UploadState.uploading => 'Uploading',
        UploadState.verifying => 'Verifying in cloud storage',
        UploadState.completed => 'Verified in cloud storage',
        UploadState.failed => 'Failed',
        UploadState.cancelled => 'Cancelled',
        UploadState.quarantined => 'Quarantined by the server',
      };

  Map<String, dynamic> toJson() => {
        'id': id,
        'org': organizationId,
        'company': companyId,
        'file': fileName,
        'name': displayName,
        'mime': mime,
        'size': size,
        'sha256': sha256,
        'path': localPath,
        'node': nodeId,
        'category': categoryId,
        'metadata': metadata,
        'key': idempotencyKey,
        'source': source.name,
        'created': createdAt.toUtc().toIso8601String(),
        'pages': pages,
        'state': state.name,
        'attempts': attempts,
        'progress': progress,
        'errCode': errorCode,
        'errMsg': errorMessage,
        'retryable': retryable,
        'op': serverOperationId,
        'doc': documentId,
        'copy': allowCopy,
        'next': nextAttemptAt?.toUtc().toIso8601String(),
        'updated': updatedAt.toUtc().toIso8601String(),
      };

  static QueueItem fromJson(Map<String, dynamic> j) => QueueItem(
        id: j['id'] as String,
        organizationId: j['org'] as String,
        companyId: j['company'] as String,
        fileName: j['file'] as String,
        displayName: j['name'] as String,
        mime: j['mime'] as String,
        size: (j['size'] as num).toInt(),
        sha256: j['sha256'] as String,
        localPath: j['path'] as String,
        nodeId: j['node'] as String,
        categoryId: j['category'] as String?,
        metadata: ((j['metadata'] as Map?) ?? {}).cast<String, Object?>(),
        idempotencyKey: j['key'] as String,
        source: UploadSource.values.firstWhere((s) => s.name == j['source'], orElse: () => UploadSource.file),
        createdAt: DateTime.parse(j['created'] as String),
        pages: (j['pages'] as num?)?.toInt(),
        state: UploadState.values.firstWhere((s) => s.name == j['state'], orElse: () => UploadState.waiting),
        attempts: (j['attempts'] as num?)?.toInt() ?? 0,
        progress: (j['progress'] as num?)?.toDouble() ?? 0,
        errorCode: j['errCode'] as String?,
        errorMessage: j['errMsg'] as String?,
        retryable: (j['retryable'] as bool?) ?? true,
        serverOperationId: j['op'] as String?,
        documentId: j['doc'] as String?,
        allowCopy: (j['copy'] as bool?) ?? false,
        nextAttemptAt: j['next'] == null ? null : DateTime.parse(j['next'] as String),
        updatedAt: DateTime.parse(j['updated'] as String),
      );
}
