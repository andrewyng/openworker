#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { getProtocol } from './protocols.js';

/**
 * Implemention of the get-manifest hook that provides manifest information.
 * Printed as a well-formatted structure of project manifest data to stdout.
 */

if (fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const protocol = getProtocol(process.argv.slice(1));
  const cwd = process.cwd();
  const manifest = getManifestData(cwd);
  protocol.respond(JSON.stringify(manifest));
}

/**
 * Returns parsed manifest data for a project.
 * @param {string} searchDir - The path to begin searching in.
 * @returns {object} Parsed values of the project manifest.
 */
export default function getManifestData(searchDir) {
  const manifestJSON = readManifestJSONFile(searchDir, 'manifest.json');
  if (!manifestJSON) {
    throw new Error('Failed to find a manifest file in this project');
  }
  return manifestJSON;
}

/**
 * Returns a manifest.json if it exists, null otherwise.
 * @param {string} searchDir - Directory to begin search in.
 * @param {string} filename - The file to search for.
 * @returns {object | undefined} The found manifest.json object.
 */
function readManifestJSONFile(searchDir, filename) {
  try {
    const jsonFilePath = find(searchDir, filename);
    if (jsonFilePath && fs.existsSync(jsonFilePath)) {
      return JSON.parse(fs.readFileSync(jsonFilePath, 'utf8'));
    }
  } catch (err) {
    let message = 'Failed to parse the manifest file for this project';
    if (err instanceof Error) {
      message += `\n${err.name}: ${err.message}`;
    }
    throw new Error(message);
  }
  return undefined;
}

/**
 * Searches for a certain file in the provided file path.
 * @param {string} currentPath - The current path to begin the search in.
 * @param {string} targetFilename - Filename to find and match.
 * @returns {string | undefined} Full file path relative to starting path.
 */
function find(currentPath, targetFilename) {
  //  TODO Cache searched paths and check that they haven't been explored already
  //  This guards against rare edge case of a subdir in the file tree which is
  //  symlinked back to root or in such a way that creates a cycle. Can also implement
  //  max depth check.
  if (fs.existsSync(path.join(currentPath, targetFilename))) {
    return path.join(currentPath, targetFilename);
  }

  /** @type {string | undefined} */
  let targetPath;
  if (fs.existsSync(currentPath) && fs.lstatSync(currentPath).isDirectory()) {
    const dirents = fs.readdirSync(currentPath);
    dirents.some((entry) => {
      if (entry !== 'node_modules') {
        const newPath = path.resolve(currentPath, entry);
        const foundEntry = find(newPath, targetFilename);
        if (foundEntry) {
          targetPath = foundEntry;
          return true;
        }
      }
      return false;
    });
  }
  return targetPath;
}
