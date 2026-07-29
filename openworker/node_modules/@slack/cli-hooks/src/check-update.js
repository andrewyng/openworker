#!/usr/bin/env node

import childProcess from 'node:child_process';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import util from 'node:util';
import { clean, gt, major } from 'semver';

import { getProtocol } from './protocols.js';

const SLACK_BOLT_SDK = '@slack/bolt';
const SLACK_CLI_HOOKS = '@slack/cli-hooks';

/**
 * Implementation of the check-update hook that finds available SDK updates.
 * Prints an object detailing information on Slack dependencies for the CLI.
 */

if (fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const protocol = getProtocol(process.argv.slice(1));
  checkForSDKUpdates(process.cwd()).then(JSON.stringify).then(protocol.respond);
}

/**
 * @typedef {object} UpdateInfo
 * @property {string} name - Overall identifier of the package.
 * @property {ReleaseInfo[]} releases - Collection of new releases.
 * @property {string | undefined} message - Details about updates.
 * @property {string | undefined} url - More information to inspect.
 * @property {ErrorInfo | undefined} error - Notes of any failures.
 */

/**
 * @typedef {object} ReleaseInfo
 * @property {string} name - Dependency identifier on registry.
 * @property {string | undefined} current - Version in current project.
 * @property {string | undefined} latest - Most recent version available.
 * @property {boolean} update - If latest version is newer than current.
 * @property {boolean} breaking - If latest version requires a major bump.
 * @property {string | undefined} message - Details about the dependency.
 * @property {string | undefined} url - More information to inspect.
 * @property {ErrorInfo | undefined} error - Notes of any failures.
 */

/**
 * @typedef {object} ErrorInfo
 * @property {string} message - Details about the error.
 */

/**
 * File that cannot be accessed for some unexpected reason.
 * @typedef {object} InaccessibleFile
 * @property {string} name - Identifier of the file.
 * @property {unknown} error - Cause of the failure.
 */

/**
 * Checks for available SDK updates of specified Slack dependencies.
 * @param {string} cwd - The current working directory of the CLI project.
 * @returns {Promise<UpdateInfo>} Formatted package version information.
 */
export default async function checkForSDKUpdates(cwd) {
  const { versionMap, inaccessibleFiles } = await getProjectDependencies(cwd);
  const checkUpdateResponse = createCheckUpdateResponse(versionMap, inaccessibleFiles);
  return checkUpdateResponse;
}

/**
 * @typedef ProjectDependencies
 * @property {Record<string, ReleaseInfo>} versionMap -
 * @property {InaccessibleFile[]} inaccessibleFiles -
 *   Array of files that could not be read or accessed.
 */

/**
 * Gathers version information about Slack packages that are project dependencies.
 * @param {string} cwd - The current working directory of the CLI project.
 * @returns {Promise<ProjectDependencies>} a map of version information and any encountered errors
 */
async function getProjectDependencies(cwd) {
  /** @type {Record<string, ReleaseInfo>} */
  const versionMap = {};
  const { projectDependencies, inaccessibleFiles } = await gatherDependencyFile(cwd);
  try {
    if (projectDependencies.dependencies[SLACK_BOLT_SDK]) {
      versionMap[SLACK_BOLT_SDK] = await collectVersionInfo(SLACK_BOLT_SDK);
    }
    if (projectDependencies.dependencies[SLACK_CLI_HOOKS]) {
      versionMap[SLACK_CLI_HOOKS] = await collectVersionInfo(SLACK_CLI_HOOKS);
    }
  } catch (err) {
    inaccessibleFiles.push({ name: projectDependencies.fileName, error: err });
  }
  return { versionMap, inaccessibleFiles };
}

/**
 * Details about the dependencies and versioning for the current project.
 * @typedef DependencyFile
 * @property {ProjectPackages} projectDependencies - Installation information of packages.
 * @property {InaccessibleFile[]} inaccessibleFiles - Array of files that could not be read.
 */

/**
 * Mappings from dependencies to installed package information for the project.
 * @typedef {object} ProjectPackages
 * @property {string} fileName - The file with package information (package.json).
 * @property {Record<string, string>} dependencies - Install details for packages.
 */

/**
 * Gathers dependencies and version information from the project (package.json).
 * @param {string} cwd - The current working directory of the CLI project.
 * @returns {Promise<DependencyFile>} Dependencies found for the project and any encountered errors.
 */
async function gatherDependencyFile(cwd) {
  const packageJSONFileName = 'package.json';
  const projectDependencies = {
    fileName: packageJSONFileName,
    dependencies: {},
  };
  const inaccessibleFiles = [];
  try {
    const packageJSONFile = await getJSON(`${cwd}/${packageJSONFileName}`);
    if (
      'devDependencies' in packageJSONFile &&
      typeof packageJSONFile.devDependencies === 'object' &&
      packageJSONFile.devDependencies !== null &&
      Object.values(packageJSONFile.devDependencies).every((value) => typeof value === 'string')
    ) {
      Object.assign(projectDependencies.dependencies, packageJSONFile.devDependencies);
    }
    if (
      'dependencies' in packageJSONFile &&
      typeof packageJSONFile.dependencies === 'object' &&
      packageJSONFile.dependencies !== null &&
      Object.values(packageJSONFile.dependencies).every((value) => typeof value === 'string')
    ) {
      Object.assign(projectDependencies.dependencies, packageJSONFile.dependencies);
    }
  } catch (err) {
    inaccessibleFiles.push({ name: packageJSONFileName, error: err });
  }
  return { projectDependencies, inaccessibleFiles };
}

/**
 * Finds version information for a package and prepares release information.
 * @param {string} packageName - Name of the package to lookup.
 * @returns {Promise<ReleaseInfo>} Current version and release information for the package.
 */
async function collectVersionInfo(packageName) {
  /** @type {string | undefined} */
  let currentVersion;
  /** @type {string | undefined} */
  let latestVersion;
  /** @type {string | undefined} */
  let releaseNotesUrl;
  /** @type {ErrorInfo | undefined} */
  let dependencyError;
  try {
    currentVersion = await getProjectPackageVersion(packageName);
    latestVersion = await fetchLatestPackageVersion(packageName);
    if (hasAvailableUpdates(currentVersion, latestVersion)) {
      releaseNotesUrl = getReleaseNotesUrl(packageName, latestVersion);
    }
  } catch (err) {
    if (typeof err === 'string') {
      dependencyError = { message: err };
    } else if (err instanceof Error) {
      dependencyError = { message: err.message };
    }
  }
  return {
    name: packageName,
    current: currentVersion,
    latest: latestVersion,
    update: hasAvailableUpdates(currentVersion, latestVersion),
    breaking: hasBreakingChange(currentVersion, latestVersion),
    message: undefined,
    url: releaseNotesUrl,
    error: dependencyError,
  };
}

/**
 * Finds the current version of a local project package.
 * @param {string} packageName - Name of the package to lookup.
 * @returns {Promise<string>} A stringified semver of the found version.
 */
async function getProjectPackageVersion(packageName) {
  const stdout = await execWrapper(`npm list ${packageName} --depth=0 --json`);
  const currentVersionOutput = JSON.parse(stdout);
  if (!currentVersionOutput.dependencies?.[packageName]) {
    throw new Error(`Failed to gather project information about ${packageName}`);
  }
  return currentVersionOutput.dependencies[packageName].version;
}

/**
 * Gets the latest package version.
 * @param {string} packageName - Package to search for the latest version of.
 * @returns {Promise<string>} The most recent version of the published package.
 */
async function fetchLatestPackageVersion(packageName) {
  const command = `npm info ${packageName} version --tag latest`;
  const stdout = await execWrapper(command);
  return stdout;
}

/**
 * Formats the URL with the release notes for a package.
 * @param {string} packageName - The package with the release.
 * @param {string} latestVersion - Recent release version.
 * @returns {string | undefined} A URL with release notes.
 */
function getReleaseNotesUrl(packageName, latestVersion) {
  if (packageName === SLACK_BOLT_SDK) {
    return `https://github.com/slackapi/bolt-js/releases/tag/v${latestVersion}`;
  }
  if (packageName === SLACK_CLI_HOOKS) {
    return `https://github.com/slackapi/node-slack-sdk/releases/tag/@slack/cli-hooks@${latestVersion}`;
  }
  return undefined;
}

/**
 * Checks if the latest version is more recent than the current version.
 * @param {string | undefined} current - Package version available in the project.
 * @param {string | undefined} latest - Most up-to-date dependency version available.
 * @returns {boolean} If the update will result in a breaking change.
 */
export function hasAvailableUpdates(current, latest) {
  if (!current || !latest || !clean(current) || !clean(latest)) {
    return false;
  }
  return gt(latest, current);
}

/**
 * Checks if updating a dependency to the latest version causes a breaking change.
 * @param {string | undefined} current - Package version available in the project.
 * @param {string | undefined} latest - Most up-to-date dependency version available.
 * @returns {boolean} If the update will result in a breaking change.
 */
export function hasBreakingChange(current, latest) {
  if (!current || !latest || !clean(current) || !clean(latest)) {
    return false;
  }
  return major(latest) > major(current);
}

/**
 * Wraps and parses the output of the exec() function
 * @param {string} command - The command to soon be executed.
 * @returns {Promise<string>} the output from the command.
 */
async function execWrapper(command) {
  const exec = util.promisify(childProcess.exec);
  const { stdout } = await exec(command);
  return stdout.trim();
}

/**
 * Reads and parses a JSON file to return the contents.
 * @param {string} filePath - The path of the file being read.
 * @returns {Promise<object>} - The parsed file contents.
 */
async function getJSON(filePath) {
  if (fs.existsSync(filePath)) {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  }
  throw new Error(`Cannot find a file at path ${filePath}`);
}

/**
 * Creates the check update response in the format expected by the CLI.
 * @param {Record<string, ReleaseInfo>} versionMap - Information about packages and updates.
 * @param {InaccessibleFile[]} inaccessibleFiles - Array of files that could not be read.
 * @returns {UpdateInfo} Update information formatted in the expected manner.
 */
function createCheckUpdateResponse(versionMap, inaccessibleFiles) {
  /** @type {string[]} */
  const dependencyErrors = [];
  const releases = Object.entries(versionMap).map(([dependency, version]) => {
    if (version.error) {
      dependencyErrors.push(dependency);
    }
    return version;
  });
  return {
    name: 'the Slack SDK',
    message: '',
    releases,
    url: 'https://docs.slack.dev/changelog',
    error: createUpdateErrorMessage(dependencyErrors, inaccessibleFiles),
  };
}

/**
 * Prepares a message about any errors encountered.
 * @param {string[]} dependencyErrors - Packages that failed for some unexpected reason.
 * @param {InaccessibleFile[]} inaccessibleFiles - Array of files that could not be read.
 * @returns {ErrorInfo | undefined} Formatted information about errors.
 */
export function createUpdateErrorMessage(dependencyErrors, inaccessibleFiles) {
  if (dependencyErrors.length === 0 && inaccessibleFiles.length === 0) {
    return undefined;
  }
  let message = '';
  if (dependencyErrors.length > 0) {
    message = `An error occurred fetching updates for the following packages: ${dependencyErrors.join(', ')}\n`;
  }
  const fileErrors = inaccessibleFiles.map((file) => `  ${file.name}: ${file.error}\n`);
  if (inaccessibleFiles.length > 0) {
    message += `An error occurred while reading the following files:\n${fileErrors.join('')}`;
  }
  return { message };
}
