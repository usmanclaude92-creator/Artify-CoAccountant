import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../core/network/api_error.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';

class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key, required this.onContinue, this.notice});
  final VoidCallback onContinue;
  final String? notice;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Spacer(),
              Icon(Icons.document_scanner_outlined, size: 56, color: Theme.of(context).colorScheme.primary),
              const SizedBox(height: 16),
              Text('Artify Co-Accountant', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w700)),
              const SizedBox(height: 8),
              const Text('Capture and send your company documents securely. Your accountant sees them in the Artify workspace.'),
              if (notice != null) ...[const SizedBox(height: 16), Banner2(notice!, tone: Tone.warning)],
              const Spacer(flex: 2),
              FilledButton(onPressed: onContinue, child: const Text('Sign in')),
              const SizedBox(height: 8),
            ]),
          ),
        ),
      );
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _show = false;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    await widget.services.auth.login(_email.text, _password.text);
  }

  @override
  Widget build(BuildContext context) {
    final auth = widget.services.auth;
    return ListenableBuilder(
      listenable: auth,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text('Sign in')),
        body: SafeArea(
          child: ListView(padding: const EdgeInsets.all(24), children: [
            if (auth.message != null) ...[Banner2(auth.message!), const SizedBox(height: 16)],
            Form(
              key: _form,
              child: Column(children: [
                TextFormField(
                  controller: _email,
                  keyboardType: TextInputType.emailAddress,
                  autofillHints: const [AutofillHints.username, AutofillHints.email],
                  decoration: const InputDecoration(labelText: 'Email'),
                  validator: (v) => (v == null || !v.contains('@') || v.trim().length < 5) ? 'Enter your email address.' : null,
                ),
                const SizedBox(height: 16),
                TextFormField(
                  controller: _password,
                  obscureText: !_show,
                  autofillHints: const [AutofillHints.password],
                  decoration: InputDecoration(
                    labelText: 'Password',
                    suffixIcon: IconButton(tooltip: _show ? 'Hide password' : 'Show password', icon: Icon(_show ? Icons.visibility_off : Icons.visibility), onPressed: () => setState(() => _show = !_show)),
                  ),
                  validator: (v) => (v == null || v.isEmpty) ? 'Enter your password.' : null,
                  onFieldSubmitted: (_) => _submit(),
                ),
              ]),
            ),
            const SizedBox(height: 24),
            FilledButton(onPressed: auth.busy ? null : _submit, child: auth.busy ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2)) : const Text('Sign in')),
            TextButton(onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => ForgotPasswordScreen(services: widget.services))), child: const Text('Forgot password?')),
          ]),
        ),
      ),
    );
  }
}

class MfaScreen extends StatefulWidget {
  const MfaScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<MfaScreen> createState() => _MfaScreenState();
}

class _MfaScreenState extends State<MfaScreen> {
  final _code = TextEditingController();
  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final auth = widget.services.auth;
    return ListenableBuilder(
      listenable: auth,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text('Verification code')),
        body: SafeArea(
          child: ListView(padding: const EdgeInsets.all(24), children: [
            const Text('Enter the 6-digit code from your authenticator app.'),
            const SizedBox(height: 16),
            if (auth.message != null) ...[Banner2(auth.message!), const SizedBox(height: 16)],
            TextField(controller: _code, keyboardType: TextInputType.number, maxLength: 8, autofillHints: const [AutofillHints.oneTimeCode], decoration: const InputDecoration(labelText: 'Code'), onSubmitted: (v) => auth.verifyMfa(v)),
            const SizedBox(height: 8),
            FilledButton(onPressed: auth.busy ? null : () => auth.verifyMfa(_code.text), child: const Text('Verify')),
            TextButton(onPressed: auth.logout, child: const Text('Cancel')),
          ]),
        ),
      ),
    );
  }
}

class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key, required this.services});
  final AppServices services;
  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  final _email = TextEditingController();
  final _token = TextEditingController();
  final _pw = TextEditingController();
  bool _sent = false;
  bool _done = false;
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _email.dispose();
    _token.dispose();
    _pw.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() f) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await f();
    } on ApiError catch (e) {
      _error = e.message;
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final repo = widget.services.auth.repo;
    return Scaffold(
      appBar: AppBar(title: const Text('Reset password')),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(24), children: [
          if (_error != null) ...[Banner2(_error!), const SizedBox(height: 16)],
          if (_done)
            const Banner2('Your password was changed. You can sign in with the new one.', tone: Tone.success)
          else if (!_sent) ...[
            const Text('We will email a reset code to the address on your account, if it exists.'),
            const SizedBox(height: 16),
            TextField(controller: _email, keyboardType: TextInputType.emailAddress, decoration: const InputDecoration(labelText: 'Email')),
            const SizedBox(height: 16),
            FilledButton(onPressed: _busy ? null : () => _run(() async {
                  await repo.requestPasswordReset(_email.text);
                  setState(() => _sent = true);
                }), child: const Text('Send reset code')),
            TextButton(onPressed: () => setState(() => _sent = true), child: const Text('I already have a code')),
          ] else ...[
            const Text('Enter the code from the email and choose a new password (at least 12 characters).'),
            const SizedBox(height: 16),
            TextField(controller: _token, decoration: const InputDecoration(labelText: 'Reset code')),
            const SizedBox(height: 16),
            TextField(controller: _pw, obscureText: true, decoration: const InputDecoration(labelText: 'New password')),
            const SizedBox(height: 16),
            FilledButton(onPressed: _busy ? null : () => _run(() async {
                  if (_pw.text.length < 12) throw const ApiError(status: 422, code: 'weak_password', message: 'Use at least 12 characters.');
                  await repo.confirmPasswordReset(_token.text, _pw.text);
                  setState(() => _done = true);
                }), child: const Text('Change password')),
          ],
        ]),
      ),
    );
  }
}

class SessionExpiredScreen extends StatelessWidget {
  const SessionExpiredScreen({super.key, required this.onSignIn});
  final VoidCallback onSignIn;
  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: StateMessage(
            icon: Icons.lock_clock_outlined,
            title: 'Your session has ended',
            body: 'For your security you were signed out. Documents waiting to upload are kept on this phone and continue after you sign in.',
            action: FilledButton(onPressed: onSignIn, child: const Text('Sign in again')),
          ),
        ),
      );
}

class LockScreen extends StatelessWidget {
  const LockScreen({super.key, required this.services});
  final AppServices services;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: services.auth,
        builder: (context, _) => Scaffold(
          body: SafeArea(
            child: StateMessage(
              icon: Icons.fingerprint,
              title: 'Locked',
              body: services.auth.message ?? 'Unlock with your fingerprint, face or device passcode. This protects the session already on this phone; it does not replace signing in.',
              action: Column(mainAxisSize: MainAxisSize.min, children: [
                FilledButton(onPressed: services.auth.unlock, child: const Text('Unlock')),
                TextButton(onPressed: services.auth.logout, child: const Text('Sign out')),
              ]),
            ),
          ),
        ),
      );
}
