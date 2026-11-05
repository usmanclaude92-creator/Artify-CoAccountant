class Membership {
  const Membership({required this.organizationId, required this.organizationName, required this.organizationStatus, required this.organizationRole, required this.companyRoles});
  final String organizationId;
  final String organizationName;
  final String organizationStatus;
  final String? organizationRole;
  final Map<String, String> companyRoles;

  factory Membership.fromJson(Map<String, dynamic> j) => Membership(
        organizationId: j['organization_id'] as String,
        organizationName: j['organization_name'] as String,
        organizationStatus: j['organization_status'] as String,
        organizationRole: j['organization_role'] as String?,
        companyRoles: ((j['company_roles'] as Map?) ?? {}).map((k, v) => MapEntry(k as String, v as String)),
      );
}

class Me {
  const Me({required this.userId, required this.email, required this.displayName, required this.locale, required this.mfaEnabled, required this.memberships, this.permissions = const []});
  final String userId;
  final String email;
  final String displayName;
  final String locale;
  final bool mfaEnabled;
  final List<Membership> memberships;
  final List<String> permissions;

  factory Me.fromJson(Map<String, dynamic> j) => Me(
        userId: j['user_id'] as String,
        email: j['email'] as String,
        displayName: j['display_name'] as String,
        locale: (j['locale'] as String?) ?? 'en',
        mfaEnabled: (j['mfa_enabled'] as bool?) ?? false,
        memberships: ((j['memberships'] as List?) ?? []).map((m) => Membership.fromJson(m as Map<String, dynamic>)).toList(),
        permissions: ((j['permissions'] as List?) ?? []).cast<String>(),
      );
}

sealed class LoginOutcome {
  const LoginOutcome();
}

class LoggedIn extends LoginOutcome {
  const LoggedIn();
}

class MfaRequired extends LoginOutcome {
  const MfaRequired(this.mfaToken);
  final String mfaToken;
}

/// The account must enrol a second factor before it can sign in. Enrolment is done on the web or Windows app.
class MfaEnrollmentRequired extends LoginOutcome {
  const MfaEnrollmentRequired();
}
