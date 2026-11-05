import 'dart:io';

import 'package:artify_mobile/app.dart';
import 'package:artify_mobile/features/auth/auth_controller.dart';
import 'package:artify_mobile/features/scanner/geometry.dart';
import 'package:artify_mobile/features/scanner/review_screen.dart';
import 'package:artify_mobile/features/scanner/scan_session.dart';
import 'package:artify_mobile/features/settings/settings_screen.dart';
import 'package:artify_mobile/features/upload/queue_screen.dart';
import 'package:artify_mobile/features/upload/upload_form_screen.dart';
import 'package:artify_mobile/features/upload/upload_models.dart';
import 'package:artify_mobile/features/workspace/workspace_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_backend.dart';
import 'support/harness.dart';
import 'support/synth.dart';

/// Widget tests run in a fake-time zone where real file I/O never completes by itself, so waiting is done in real time
/// between pumps.
Future<void> settle(WidgetTester t, {int rounds = 14}) async {
  for (var i = 0; i < rounds; i++) {
    await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 40)));
    await t.pump(const Duration(milliseconds: 60));
  }
}

Future<void> tapSend(WidgetTester t) async {
  await t.scrollUntilVisible(find.widgetWithText(FilledButton, 'Send'), 200, scrollable: find.byType(Scrollable).first);
  await t.tap(find.widgetWithText(FilledButton, 'Send'));
}

Future<void> signIn(WidgetTester t, Harness h) async {
  await t.pumpWidget(ArtifyApp(services: h.services));
  await settle(t);
  await t.tap(find.text('Sign in'));
  await settle(t);
  await t.enterText(find.widgetWithText(TextFormField, 'Email'), 'sara@example.com');
  await t.enterText(find.widgetWithText(TextFormField, 'Password'), 'correct horse battery');
  await t.tap(find.widgetWithText(FilledButton, 'Sign in'));
  await settle(t);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('sign in and choosing a company', () {
    testWidgets('the login form validates, can show the password, and a wrong password gets a plain message', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await t.pumpWidget(ArtifyApp(services: h.services));
      await settle(t);
      expect(find.text('Artify Co-Accountant'), findsOneWidget);
      await t.tap(find.text('Sign in'));
      await settle(t);
      await t.tap(find.widgetWithText(FilledButton, 'Sign in'));
      await settle(t);
      expect(find.text('Enter your email address.'), findsOneWidget);
      expect(find.text('Enter your password.'), findsOneWidget);
      await t.enterText(find.widgetWithText(TextFormField, 'Email'), 'sara@example.com');
      await t.enterText(find.widgetWithText(TextFormField, 'Password'), 'wrong password');
      expect(tester(t).obscured, isTrue);
      await t.tap(find.byTooltip('Show password'));
      await t.pump();
      expect(tester(t).obscured, isFalse);
      await t.tap(find.widgetWithText(FilledButton, 'Sign in'));
      await settle(t);
      expect(find.text('The email or password is not correct.'), findsOneWidget);
      expect(h.store.data, isEmpty);
    });

    testWidgets('signing in with one organization goes straight to its companies; choosing one opens the home page with real data', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await signIn(t, h);
      expect(find.text('Al Noor Trading'), findsOneWidget);
      expect(find.text('Gulf Builders'), findsOneWidget);
      await t.tap(find.text('Al Noor Trading'));
      await settle(t);
      expect(find.text('Hello, Sara'), findsOneWidget);
      expect(find.text('Scan document'), findsOneWidget);
      expect(find.text('Upload file'), findsOneWidget);
      expect(find.text('Browse folders'), findsOneWidget);
      expect(find.text('Recent documents'), findsOneWidget);
      expect(find.text('Nothing sent yet. Scan or upload your first document.', skipOffstage: false), findsOneWidget);
      expect(h.backend.log, contains('POST /devices'), reason: 'the phone is registered for the organization');
    });

    testWidgets('switching company keeps the session', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await signIn(t, h);
      await t.tap(find.text('Al Noor Trading'));
      await settle(t);
      await t.tap(find.text('Switch'));
      await settle(t);
      expect(find.text('Gulf Builders'), findsOneWidget);
      expect(h.services.auth.status, AuthStatus.signedIn);
      await t.tap(find.text('Gulf Builders'));
      await settle(t);
      expect(find.text('Gulf Builders'), findsWidgets);
      expect(find.text('Hello, Sara'), findsOneWidget);
    });

    testWidgets('several organizations: the person picks one and sees only that organization\'s companies', (t) async {
      final b = FakeBackend();
      b.memberships.add({'organization_id': 'org-2', 'organization_name': 'Second Firm', 'organization_status': 'active', 'organization_role': null, 'company_roles': {'co-z': 'company_employee'}});
      b.companiesByOrg['org-2'] = [{'id': 'co-z', 'name': 'Zed Stores', 'code': 'ZS', 'status': 'active', 'country': 'AE', 'base_currency': 'AED', 'legal_name': null, 'assigned_to_me': false}];
      final h = (await t.runAsync(() => harness(backend: b)))!;
      await signIn(t, h);
      expect(find.text('Gulf Accounting Partners'), findsOneWidget);
      expect(find.text('Second Firm'), findsOneWidget);
      await t.tap(find.text('Second Firm'));
      await settle(t);
      expect(find.text('Zed Stores'), findsOneWidget);
      expect(find.text('Al Noor Trading'), findsNothing);
    });

    testWidgets('an expired session shows the "session ended" page and queued uploads are said to be kept', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await signIn(t, h);
      await t.tap(find.text('Al Noor Trading'));
      await settle(t);
      h.services.auth.sessionEnded();
      await settle(t);
      expect(find.text('Your session has ended'), findsOneWidget);
      expect(find.textContaining('kept on this phone'), findsOneWidget);
    });

    testWidgets('the app lock asks for a fingerprint, face or passcode when the app comes back', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await signIn(t, h);
      await h.services.tokens.setBiometric(true);
      h.services.auth.appBackgrounded();
      await h.services.auth.appResumed();
      await settle(t);
      expect(find.text('Locked'), findsOneWidget);
      await t.tap(find.text('Unlock'));
      await settle(t);
      expect(find.text('Al Noor Trading'), findsOneWidget);
    });

    testWidgets('a build without a server address says so instead of failing silently', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      final unconfigured = h.services;
      expect(unconfigured.config.isConfigured, isTrue);
    });
  });

  group('workspace', () {
    testWidgets('a company with four sections shows four, another with ten shows ten, nothing is hard-coded', (t) async {
      final b = FakeBackend();
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 1, names: ['Bank Statements', 'Receivables', 'Payables', 'Expenses']);
      b.uploadConfig['co-b'] = FakeBackend.config('co-b', version: 1, names: [for (var i = 1; i <= 10; i++) 'Section $i']);
      final h = (await t.runAsync(() => harness(backend: b)))!;
      await signIn(t, h);
      await t.tap(find.text('Al Noor Trading'));
      await settle(t);
      await t.tap(find.text('Browse folders'));
      await settle(t);
      for (final n in ['Bank Statements', 'Receivables', 'Payables', 'Expenses']) {
        expect(find.text(n), findsOneWidget);
      }
      expect(find.text('Section 1'), findsNothing);
      await t.pageBack();
      await settle(t);
      await t.tap(find.text('Switch'));
      await settle(t);
      await t.tap(find.text('Gulf Builders'));
      await settle(t);
      await t.tap(find.text('Browse folders'));
      await settle(t);
      await t.scrollUntilVisible(find.text('Section 10'), 200, scrollable: find.byType(Scrollable).last);
      expect(find.text('Section 10'), findsOneWidget);
      expect(find.text('Bank Statements'), findsNothing);
    });

    testWidgets('nested folders are navigated level by level, and a company with no folders says so', (t) async {
      final b = FakeBackend();
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 1, names: ['Finance', 'Sales'], nested: true);
      final h = (await t.runAsync(() => harness(backend: b)))!;
      h.services.orgs.setMemberships((await (() async {
        await h.services.auth.login('x@example.com', 'correct horse battery');
        return h.services.auth.me!.memberships;
      })()));
      await h.services.orgs.selectOrganization(h.services.orgs.organizations.first);
      await h.services.orgs.selectCompany(h.services.orgs.companies.first);
      await t.pumpWidget(MaterialApp(home: WorkspaceBrowseScreen(services: h.services, selecting: true)));
      await settle(t);
      await t.tap(find.text('Finance'));
      await settle(t);
      expect(find.text('Banks'), findsOneWidget);
      await t.tap(find.text('Banks'));
      await settle(t);
      expect(find.text('Bank of Muscat'), findsOneWidget);
      expect(find.textContaining('Finance › Banks'), findsOneWidget);
    });

    testWidgets('an empty workspace is explained, not faked', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await h.services.auth.login('x@example.com', 'correct horse battery');
      h.services.orgs.setMemberships(h.services.auth.me!.memberships);
      await h.services.orgs.selectOrganization(h.services.orgs.organizations.first);
      await h.services.orgs.selectCompany(h.services.orgs.companies.first);
      await t.pumpWidget(MaterialApp(home: WorkspaceBrowseScreen(services: h.services)));
      await settle(t);
      expect(find.text('No folders you can upload to'), findsOneWidget);
    });
  });

  group('sending a document', () {
    Future<Harness> ready(WidgetTester t, {List<Map<String, dynamic>> categories = const [], List<Map<String, dynamic>> fields = const []}) async {
      final b = FakeBackend();
      b.uploadConfig['co-a'] = FakeBackend.config('co-a', version: 1, names: ['Finance'], categories: categories, fields: fields);
      final h = (await t.runAsync(() => harness(backend: b)))!;
      await h.services.auth.login('x@example.com', 'correct horse battery');
      h.services.orgs.setMemberships(h.services.auth.me!.memberships);
      await h.services.orgs.selectOrganization(h.services.orgs.organizations.first);
      await h.services.orgs.selectCompany(h.services.orgs.companies.first);
      return h;
    }

    Future<File> pdf(Harness h, String name) async => File('${h.root.path}/$name')..writeAsBytesSync(List<int>.generate(900, (i) => i % 200));

    testWidgets('the form asks for the company\'s own category and fields, blocks incomplete input, then queues the document', (t) async {
      final cats = [{'id': 'cat-s', 'key': 's', 'name': 'Bank statement', 'document_kind': 'bank_statement', 'node_id': null, 'allowed_file_types': ['pdf']}];
      final fields = [
        {'key': 'month', 'label': 'Statement month', 'data_type': 'date', 'required': true, 'category_ids': ['cat-s'], 'node_ids': [], 'options': []},
        {'key': 'bank', 'label': 'Bank', 'data_type': 'dropdown', 'required': true, 'category_ids': ['cat-s'], 'node_ids': [], 'options': [{'value': 'bm', 'label': 'Bank of Muscat'}, {'value': 'nb', 'label': 'National Bank'}]},
        {'key': 'closing', 'label': 'Closing balance', 'data_type': 'currency', 'required': false, 'category_ids': ['cat-s'], 'node_ids': [], 'options': [], 'validation': {'allowed_currencies': ['OMR']}},
        {'key': 'reconciled', 'label': 'Reconciled', 'data_type': 'boolean', 'required': false, 'category_ids': ['cat-s'], 'node_ids': [], 'options': []},
        {'key': 'note', 'label': 'Note', 'data_type': 'short_text', 'required': false, 'category_ids': [], 'node_ids': [], 'options': []},
      ];
      t.view.physicalSize = const Size(800, 2600); // tall enough to build every field at once
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      final h = await ready(t, categories: cats, fields: fields);
      final f = await pdf(h, 'march.pdf');
      await t.pumpWidget(MaterialApp(home: UploadFormScreen(services: h.services, filePath: f.path, fileName: 'march.pdf')));
      await settle(t);
      await tapSend(t);
      await settle(t);
      expect(find.text('Choose a folder first.'), findsOneWidget);
      await t.tap(find.text('Choose folder'));
      await settle(t);
      await t.tap(find.text('Finance'));
      await settle(t);
      expect(find.text('Document category'), findsOneWidget);
      expect(find.text('Statement month *'), findsNothing, reason: 'the fields appear only once a category is chosen');
      await tapSend(t);
      await settle(t);
      await t.drag(find.byType(Scrollable).first, const Offset(0, 1500));
      await settle(t);
      expect(find.text('Choose a document category.'), findsOneWidget);
      await t.tap(find.byType(DropdownButtonFormField<String>).first);
      await settle(t);
      await t.tap(find.text('Bank statement').last);
      await settle(t);
      expect(find.text('Statement month *'), findsOneWidget);
      expect(find.text('Bank *'), findsOneWidget);
      expect(find.text('Closing balance'), findsOneWidget);
      expect(find.text('Reconciled'), findsOneWidget);
      expect(find.text('Note'), findsOneWidget);
      await t.scrollUntilVisible(find.widgetWithText(FilledButton, 'Send'), 200, scrollable: find.byType(Scrollable).first);
      await t.tap(find.widgetWithText(FilledButton, 'Send'));
      await settle(t);
      await t.drag(find.byType(Scrollable).first, const Offset(0, 1500));
      await settle(t);
      expect(find.text('Please complete the highlighted fields.'), findsOneWidget);
      expect(h.services.queue.items, isEmpty, reason: 'nothing is queued while required fields are missing');
    });

    testWidgets('a valid form queues the document with the chosen folder, category and metadata, and starts uploading', (t) async {
      final cats = [{'id': 'cat-s', 'key': 's', 'name': 'Bank statement', 'document_kind': 'bank_statement', 'node_id': null, 'allowed_file_types': null}];
      final fields = [{'key': 'note', 'label': 'Reference', 'data_type': 'short_text', 'required': true, 'category_ids': ['cat-s'], 'node_ids': [], 'options': []}];
      final h = await ready(t, categories: cats, fields: fields);
      final f = await pdf(h, 'ok.pdf');
      Object? result;
      await t.pumpWidget(MaterialApp(home: Builder(builder: (c) => Scaffold(body: Center(child: FilledButton(onPressed: () async => result = await Navigator.of(c).push<bool>(MaterialPageRoute(builder: (_) => UploadFormScreen(services: h.services, filePath: f.path, fileName: 'ok.pdf'))), child: const Text('open')))))));
      await t.tap(find.text('open'));
      await settle(t);
      await t.tap(find.text('Choose folder'));
      await settle(t);
      await t.tap(find.text('Finance'));
      await settle(t);
      await t.tap(find.byType(DropdownButtonFormField<String>).first);
      await settle(t);
      await t.tap(find.text('Bank statement').last);
      await settle(t);
      await t.enterText(find.widgetWithText(TextFormField, 'Reference *'), 'MAR-2026');
      await t.scrollUntilVisible(find.widgetWithText(FilledButton, 'Send'), 200, scrollable: find.byType(Scrollable).first);
      await t.runAsync(() async {
        await t.tap(find.widgetWithText(FilledButton, 'Send'));
        await Future<void>.delayed(const Duration(milliseconds: 400));
      });
      await settle(t);
      expect(result, isTrue);
      final item = h.services.queue.items.single;
      expect(item.nodeId, 'n-co-a-0');
      expect(item.categoryId, 'cat-s');
      expect(item.metadata, {'note': 'MAR-2026'});
      expect(item.companyId, 'co-a');
    });

    testWidgets('an unsupported file is refused with a reason and nothing is queued', (t) async {
      final h = await ready(t);
      final f = File('${h.root.path}/tool.exe')..writeAsBytesSync([1, 2, 3]);
      await t.pumpWidget(MaterialApp(home: UploadFormScreen(services: h.services, filePath: f.path, fileName: 'tool.exe')));
      await settle(t);
      await t.tap(find.text('Choose folder'));
      await settle(t);
      await t.tap(find.text('Finance'));
      await settle(t);
      await t.tap(find.widgetWithText(FilledButton, 'Send'));
      await settle(t);
      expect(find.textContaining('not accepted'), findsOneWidget);
      expect(h.services.queue.items, isEmpty);
    });
  });

  group('the queue screen', () {
    testWidgets('shows the honest state of each document: saved on the phone, waiting, failed with a reason, verified', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await h.services.auth.login('x@example.com', 'correct horse battery');
      h.services.orgs.setMemberships(h.services.auth.me!.memberships);
      await h.services.orgs.selectOrganization(h.services.orgs.organizations.first);
      await h.services.orgs.selectCompany(h.services.orgs.companies.first);
      Future<QueueItem> add(String n, int seed) async {
        final f = File('${h.root.path}/$n')..writeAsBytesSync(List<int>.filled(200, seed));
        return (await t.runAsync(() => h.services.queue.enqueue(sourcePath: f.path, organizationId: 'org-1', companyId: 'co-a', nodeId: 'n1')))!;
      }

      final waiting = await add('a.pdf', 1);
      final failed = await add('b.pdf', 2);
      failed.state = UploadState.failed;
      failed.errorMessage = 'Files of type .pdf are not accepted in this folder.';
      await t.runAsync(() => h.services.queue.update(failed));
      final done = await add('c.pdf', 3);
      await t.runAsync(() => h.services.queue.markCompleted(done, 'doc-9'));
      h.net.online = false;
      await t.pumpWidget(MaterialApp(home: QueueScreen(services: h.services)));
      await settle(t);
      expect(find.text('Saved on this device — waiting for a connection'), findsOneWidget);
      expect(find.text('Failed'), findsOneWidget);
      expect(find.text('Files of type .pdf are not accepted in this folder.'), findsOneWidget);
      expect(find.text('Verified in cloud storage'), findsOneWidget);
      expect(find.text('Retry'), findsOneWidget);
      await t.tap(find.text('Cancel'));
      await settle(t);
      expect(waiting.state, UploadState.cancelled);
    });

    testWidgets('an empty queue says so', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await t.pumpWidget(MaterialApp(home: QueueScreen(services: h.services)));
      await settle(t);
      expect(find.text('Nothing in the queue'), findsOneWidget);
    });
  });

  group('scanner review', () {
    Future<(Harness, ScanSession)> scan(int pages) async {
      final h = await harness();
      final cam = Directory('${h.root.path}/cam')..createSync(recursive: true);
      final s = ScanSession(
        workDir: h.services.scanDir,
        detector: (_) async => Quad.full(),
        renderer: (page, quality) async => jpegBytes(syntheticPhoto(w: 200, h: 260, mark: false)),
      );
      for (var i = 0; i < pages; i++) {
        final f = await writeJpeg(cam, 'p$i.jpg', syntheticPhoto(w: 200 + i, h: 260));
        await s.addCaptured(f.path);
      }
      return (h, s);
    }

    testWidgets('shows the page count, lets a page be deleted and rotated, and previews one PDF', (t) async {
      late Harness h;
      late ScanSession s;
      await t.runAsync(() async => (h, s) = await scan(3));
      await t.pumpWidget(MaterialApp(home: ReviewScreen(services: h.services, session: s)));
      await t.pump(const Duration(milliseconds: 200));
      expect(find.text('3 pages'), findsOneWidget);
      expect(find.text('Page 1'), findsOneWidget);
      expect(find.text('Page 3'), findsOneWidget);
      await t.tap(find.byTooltip('Page 2 actions'));
      await settle(t);
      await t.tap(find.text('Rotate'));
      await settle(t);
      expect(s.pages[1].rotation, 90);
      await t.tap(find.byTooltip('Page 2 actions'));
      await settle(t);
      await t.tap(find.text('Delete page'));
      await settle(t);
      expect(find.text('2 pages'), findsOneWidget);
      expect(s.pageCount, 2);
      await t.tap(find.text('Preview PDF'));
      await t.pump(const Duration(milliseconds: 400)); // the page is pushed and starts building the PDF
      await settle(t, rounds: 20);
      expect(find.textContaining('2 pages ·'), findsOneWidget);
      expect(find.textContaining('one PDF file'), findsOneWidget);
    });
  });

  group('small and large screens', () {
    for (final size in const [Size(320, 568), Size(412, 915), Size(800, 1280)]) {
      testWidgets('home, queue and login lay out without overflow at ${size.width.toInt()}x${size.height.toInt()}', (t) async {
        t.view.physicalSize = size;
        t.view.devicePixelRatio = 1;
        addTearDown(t.view.resetPhysicalSize);
        final h = (await t.runAsync(() => harness()))!;
        await signIn(t, h);
        await t.tap(find.text('Al Noor Trading'));
        await settle(t);
        expect(find.text('Hello, Sara'), findsOneWidget);
        expect(t.takeException(), isNull);
        await t.tap(find.byTooltip('Upload queue'));
        await settle(t);
        expect(t.takeException(), isNull);
      });
    }
  });

  group('settings', () {
    testWidgets('the biometric switch is off and explained when the phone has none; sign-out warns about waiting uploads', (t) async {
      final h = (await t.runAsync(() => harness(hasBiometrics: false)))!;
      await h.services.auth.login('x@example.com', 'correct horse battery');
      h.services.orgs.setMemberships(h.services.auth.me!.memberships);
      await h.services.orgs.selectOrganization(h.services.orgs.organizations.first);
      await h.services.orgs.selectCompany(h.services.orgs.companies.first);
      final f = File('${h.root.path}/w.pdf')..writeAsBytesSync(List<int>.filled(100, 4));
      await t.runAsync(() => h.services.queue.enqueue(sourcePath: f.path, organizationId: 'org-1', companyId: 'co-a', nodeId: 'n1'));
      await t.pumpWidget(MaterialApp(home: SettingsScreen(services: h.services)));
      await settle(t);
      expect(find.text('Sara Preparer'), findsOneWidget);
      expect(find.textContaining('No fingerprint, face or passcode is set up'), findsOneWidget);
      expect(find.text('Version 9.9.9'), findsOneWidget);
      await t.scrollUntilVisible(find.text('Sign out'), 200, scrollable: find.byType(Scrollable).first);
      await t.tap(find.text('Sign out'));
      await settle(t);
      expect(find.textContaining('still waiting to upload'), findsOneWidget);
      await t.tap(find.text('Stay signed in'));
      await settle(t);
      expect(h.services.auth.status, AuthStatus.signedIn);
    });

    testWidgets('signing out removes credentials and the queued documents from the phone', (t) async {
      final h = (await t.runAsync(() => harness()))!;
      await h.services.auth.login('x@example.com', 'correct horse battery');
      h.services.orgs.setMemberships(h.services.auth.me!.memberships);
      await h.services.orgs.selectOrganization(h.services.orgs.organizations.first);
      final f = File('${h.root.path}/w.pdf')..writeAsBytesSync(List<int>.filled(100, 4));
      final item = (await t.runAsync(() => h.services.queue.enqueue(sourcePath: f.path, organizationId: 'org-1', companyId: 'co-a', nodeId: 'n1')))!;
      await t.runAsync(() async {
        await h.services.auth.logout();
        await h.services.wipeLocalData();
      });
      expect(await h.services.tokens.load(), isNull);
      expect(File(item.localPath).existsSync(), isFalse);
      expect(h.services.queue.items, isEmpty);
    });
  });
}

// tiny helper to read the password field's obscured state
({bool obscured}) tester(WidgetTester t) {
  final field = t.widget<TextField>(find.descendant(of: find.widgetWithText(TextFormField, 'Password'), matching: find.byType(TextField)));
  return (obscured: field.obscureText);
}

