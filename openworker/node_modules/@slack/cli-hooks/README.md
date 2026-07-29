# Slack CLI Hooks

[![codecov](https://codecov.io/gh/slackapi/node-slack-sdk/graph/badge.svg?token=OcQREPvC7r&flag=cli-hooks)](https://codecov.io/gh/slackapi/node-slack-sdk)

The `@slack/cli-hooks` package contains scripts that implement the contract
between the [Slack CLI][cli] and [Bolt for JavaScript][bolt].

## Overview

This library enables inter-process communication between the Slack CLI and
applications built with Bolt for JavaScript.

When used together, the CLI delegates various tasks to the Bolt application by
invoking processes ("hooks") and then making use of the responses provided by
each hook's `stdout`.

For a complete list of available hooks, read the [Supported Hooks][supported]
section.

## Requirements

This package supports Node v20 and higher. It's highly recommended to use [the
latest LTS version of Node][node].

An updated version of the Slack CLI is also encouraged while using this package.

## Installation

Add this package as a development dependency for your project with the following
command:

```sh
$ npm install --save-dev @slack/cli-hooks
```

Follow [the installation guide][install] to download the Slack CLI and easily
run the scripts included in this package.

## Usage

A Slack CLI-compatible Slack application includes a `slack.json` file that
contains hooks specific to that project. Each hook is associated with commands
that are available in the Slack CLI. By default, `get-hooks` retrieves all of
the [supported hooks][supported] and their corresponding scripts as defined in
this package.

The CLI will try to use the version of the `@slack/cli-hooks` specified in your
application's `package.json`. The hooks in this package are automatically added
to the `./node_modules/.bin` directory of your application when this package is
installed.

### Supported Hooks

The hooks that are currently supported for use within the Slack CLI include
`check-update`, `doctor`, `get-hooks`, `get-manifest`, and `start`:

| Hook Name      | CLI Command      | File |Description |
| -------------- | ---------------- | ---- | ----------- |
| `check-update` | `slack update`   | [`check-update.js`](./src/check-update.js) | Checks the project's Slack dependencies to determine whether or not any packages need to be updated. |
| `doctor`       | `slack doctor`   | [`doctor.js`](./src/doctor.js) | Returns runtime versions and other system dependencies required by the application. |
| `get-hooks`    | All              | [`get-hooks.js`](./src/get-hooks.js) | Fetches the list of available hooks for the CLI from this repository. |
| `get-manifest` | `slack manifest` | [`get-manifest.js`](./src/get-manifest.js) | Converts a `manifest.json` file into a valid manifest JSON payload. |
| `start`        | `slack run`      | [`start.js`](./src/start.js) | While developing locally, the CLI manages a socket connection with Slack's backend and utilizes this hook for events received via this connection. |

### Overriding Hooks

To customize the behavior of a hook, add the hook to your application's
`slack.json` file and provide a corresponding script to be executed.

When commands are run, the Slack CLI will look to the project's hook definitions
and use those instead of what's defined in this library, if provided. Only
[supported hooks][supported] will be recognized and executed by the Slack CLI.

Below is an example `slack.json` file that overrides the default `start` hook:

```json
{
  "hooks": {
    "get-hooks": "NODE_NO_WARNINGS=1 npx -q --no-install -p @slack/cli-hooks slack-cli-get-hooks",
    "start": "npm run dev"
  }
}
```

### Troubleshooting

Sometimes the hook scripts are installed globally and might not be automatically
updated. To determine the source of these scripts, check the `node_modules/.bin`
directory of your project then run the following command:

```sh
$ which npx slack-cli-get-hooks # macOS / Linux
```

```cmd
C:\> where.exe npx slack-cli-get-hooks # Windows
```

These hooks can be safely removed and reinstalled at your application directory
to ensure you're using the correct version for your project.

## Getting help

If you get stuck, we're here to help. The following are the best ways to get
assistance working through your issue:

* [Issue Tracker][issues] for questions, feature requests, bug reports and
  general discussion related to these packages. Try searching before you create
  a new issue.
* [Email us][email]: `developers@slack.com`
* [Community Slack][community]: a Slack community for developers building all
  kinds of Slack apps. You can find the maintainers and users of these packages
  in **#lang-javascript**.

<!-- a collection of links -->
[bolt]: https://github.com/slackapi/bolt-js
[cli]: https://docs.slack.dev/tools/slack-cli/
[community]: https://community.slack.com/
[config]: https://api.slack.com/apps
[email]: mailto:developers@slack.com
[install]: https://docs.slack.dev/tools/slack-cli/guides/installing-the-slack-cli-for-mac-and-linux
[issues]: http://github.com/slackapi/node-slack-sdk/issues
[manifest]: https://docs.slack.dev/reference/app-manifest
[node]: https://github.com/nodejs/Release#release-schedule
[supported]: #supported-hooks
