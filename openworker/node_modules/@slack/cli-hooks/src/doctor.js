#!/usr/bin/env node

import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

import { getProtocol } from './protocols.js';

/**
 * Implementation of the optional doctor script hook for the Slack CLI.
 * Printed as an object containing information about the system runtime.
 */

if (fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const protocol = getProtocol(process.argv.slice(1));
  protocol.respond(JSON.stringify(doctor()));
}

/**
 * Standardized communication format between the SDK and CLI regarding runtimes.
 * @typedef DoctorResponse
 * @property {RuntimeVersion[]} versions - Existing system dependencies present.
 */

/**
 * Information about all of the installed runtime dependencies.
 * @typedef RuntimeVersion
 * @property {string} name - Name of the runtime dependency.
 * @property {string} current - Version found on the system.
 */

/**
 * Contains available hooks and other configurations available to the SDK.
 * @returns {DoctorResponse} Information about the hooks currently supported.
 */
export default function doctor() {
  return {
    versions: [
      {
        name: 'node',
        current: process.versions.node,
      },
      {
        name: 'v8',
        current: process.versions.v8,
      },
      {
        name: 'modules',
        current: process.versions.modules,
      },
    ],
  };
}
