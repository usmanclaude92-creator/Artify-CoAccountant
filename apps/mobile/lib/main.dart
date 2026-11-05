import 'package:flutter/material.dart';

import 'app.dart';
import 'app_services.dart';
import 'core/config/app_config.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  const config = AppConfig.fromEnvironment;
  final services = await AppServices.create(config);
  runApp(ArtifyApp(services: services));
}
