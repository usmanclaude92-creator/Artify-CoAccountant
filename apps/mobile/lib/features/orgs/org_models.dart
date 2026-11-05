class CompanyRef {
  const CompanyRef({required this.id, required this.name, required this.code, required this.status, required this.country, required this.baseCurrency, this.legalName, this.assignedToMe = false});
  final String id;
  final String name;
  final String code;
  final String status;
  final String country;
  final String baseCurrency;
  final String? legalName;
  final bool assignedToMe;

  bool get isActive => status == 'active';

  factory CompanyRef.fromJson(Map<String, dynamic> j) => CompanyRef(
        id: j['id'] as String,
        name: j['name'] as String,
        code: (j['code'] as String?) ?? '',
        status: (j['status'] as String?) ?? 'active',
        country: (j['country'] as String?) ?? '',
        baseCurrency: (j['base_currency'] as String?) ?? '',
        legalName: j['legal_name'] as String?,
        assignedToMe: (j['assigned_to_me'] as bool?) ?? false,
      );
}
