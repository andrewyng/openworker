import minimist from 'minimist';

/**
 * An interface encapsulating a specific set of communication rules that the
 * SDK and the CLI both implement.
 * @typedef {object} Protocol
 * @property {string} name -
 *   Label representing the name of the protocol as expected by the Slack CLI.
 * @property {typeof console.log} log -
 *   Logging utility to surface diagnostic info from the SDK or username code.
 * @property {typeof console.error} error -
 *   Logging utility to surface errors from the SDK or username code.
 * @property {typeof console.warn} warn -
 *   Logging utility to surface warnings from the SDK or username code.
 * @property {(data: string) => void} respond -
 *   Utility method for responding to hook invocations with stringified JSON.
 * @property {() => string[]} [getCLIFlags] -
 *   Retrieves all command-line flags related to the protocol implementation.
 *   Most useful when child processes are being spawned by the SDK.
 * @property {() => void} [install] -
 *   Optional instructions for the protocol to install itself into the runtime.
 *   Similar to stubbing functionality in mocking or testing setup utilties.
 *   Ensures that the protocol expectations are met by userland and SDK code.
 * @property {() => void} [uninstall] -
 *   Optional instructions for the protocol to remove itself from the runtime.
 */

const DEFAULT_PROTOCOL = 'default';
const MSG_BOUNDARY_PROTOCOL = 'message-boundaries';
export const SUPPORTED_NAMED_PROTOCOLS = [MSG_BOUNDARY_PROTOCOL];

/**
 * The default CLI-SDK protocol. All responses in this protocol go to stdout.
 * The CLI combines both stdout and stderr to interpret the hook response.
 * This simplistic protocol has inherent limitation: no logging diagnostics!
 * @param {string[]} args - Command-line arguments passed to this process.
 * @returns {Protocol} Specified communication rules for the SDK to follow.
 */
export function DefaultProtocol(args) {
  const { manifest: manifestOnly = false } = parseArgs(args);

  // If the particular hook invocation is requesting manifest generation we
  // ensure any logging is a no-op to prevent littering stdout with logging
  // and confusing the CLI's manifest JSON payload parsing.
  const loggerMethod = manifestOnly ? () => {} : console.log;
  return {
    name: DEFAULT_PROTOCOL,
    log: loggerMethod,
    error: loggerMethod,
    warn: loggerMethod,
    respond: console.log,
  };
}

/**
 * Protocol implementation that uses both stdout and stderr for logs and alerts,
 * but also uses message boundaries to differentiate between application created
 * diagnostic information and hook responses.
 * @param {string[]} args - Command-line arguments passed to this process.
 * @returns {Protocol} Specified communication rules for the SDK to follow.
 */
export function MessageBoundaryProtocol(args) {
  const { boundary } = parseArgs(args);
  if (!boundary) throw new Error('No boundary argument provided!');
  const protocol = {
    name: MSG_BOUNDARY_PROTOCOL,
    log: console.log,
    error: console.error,
    warn: console.warn,
    respond: (/** @type {any} */ data) => {
      console.log(boundary + data + boundary);
    },
    getCLIFlags: () => [`--protocol=${MSG_BOUNDARY_PROTOCOL}`, `--boundary=${boundary}`],
  };
  return protocol;
}

// A map of protocol names to protocol implementations
const PROTOCOL_MAP = {
  [SUPPORTED_NAMED_PROTOCOLS[0]]: MessageBoundaryProtocol,
};

/**
 * Determines the protocol implementation to use for communicating with the CLI.
 * Based on the arguments provided by the CLI to the SDK hook process.
 * @param {string[]} args - Command-line flags and arguments passed to the hook.
 * @returns {Protocol} The interface to follow when messaging to the CLI.
 */
export function getProtocol(args) {
  const { protocol: protocolRequestedByCLI } = parseArgs(args);
  if (protocolRequestedByCLI) {
    if (SUPPORTED_NAMED_PROTOCOLS.includes(protocolRequestedByCLI)) {
      const protocol = PROTOCOL_MAP[protocolRequestedByCLI];

      // Allow support for protocol implementations to either be:
      // - a function, using arguments passed to this process to
      //   dynamically instantiate a Protocol interface
      // - an object implementing the Protocol interface directly
      if (typeof protocol === 'function') {
        return protocol(args);
      }
      return protocol;
    }
  }

  // If protocol negotiation fails for any reason, return the default protocol
  return DefaultProtocol(args);
}

/**
 * Parses command line arguments into a consumable object.
 * @param {string[]} argv - Command line values.
 * @returns {import("minimist").ParsedArgs} The object of parsed arguments.
 */
function parseArgs(argv) {
  return minimist(argv);
}
