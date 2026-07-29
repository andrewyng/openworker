#!/usr/bin/env node

import childProcess from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Implementation of the start hook that begins a new process to run the app.
 * The child processes will continue until exited or interrupted by the caller.
 */

if (fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  start(process.cwd());
}

/**
 * Start hook implementation that verifies and runs an app in Socket Mode.
 * @param {string} cwd - The current working directory of the project.
 */
export default function start(cwd) {
  const customPath = process.env.SLACK_CLI_CUSTOM_FILE_PATH;
  const pkgJSONMain = getPackageJSONMain(cwd);
  const pkgJSONDefault = 'app.js';
  const fullPath = path.resolve(cwd, customPath || pkgJSONMain || pkgJSONDefault);

  const app = childProcess.spawn('node', [`${fullPath}`]);
  app.stdout.setEncoding('utf-8');
  app.stdout.on('data', (data) => {
    process.stdout.write(data);
  });
  app.stderr.on('data', (data) => {
    process.stderr.write(data);
  });
  app.on('close', (code) => {
    process.exit(code);
  });
  process.on('exit', () => {
    app.kill();
  });
  process.on('SIGINT', () => {
    app.kill('SIGINT');
    process.exit();
  });
  process.on('SIGTERM', () => {
    app.kill('SIGTERM');
    process.exit();
  });
}

/**
 * Gathers the main value from the package.json for the project if present.
 * @param {string} cwd - The current working directory of the project.
 * @returns {string | undefined} - The main script to run for the project.
 */
function getPackageJSONMain(cwd) {
  try {
    const packageJSONPath = path.join(cwd, 'package.json');
    const { main } = JSON.parse(fs.readFileSync(packageJSONPath, 'utf-8'));
    return main;
  } catch {
    return undefined;
  }
}
