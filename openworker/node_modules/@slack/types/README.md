# Slack Types

The `@slack/types` package is intended to be used as a central location for modeling Slack interfaces, types, payloads
and constructs of all kinds.

## Requirements

This package supports Node v20 and higher. It's highly recommended to use [the latest LTS version of
node](https://github.com/nodejs/Release#release-schedule), and the documentation is written using syntax and features
from that version.

## Installation

```shell
$ npm install @slack/types
```

## Usage

This package exports many different types and interfaces. It is best to peruse the source code to see what is
available, but a brief list:

- Block Kit blocks and elements (`./src/block-kit/*`)
- Message attachments (`./src/message-attachments.ts`)

## Getting Help

If you get stuck, we're here to help. The following are the best ways to get assistance working through your issue:

  * [Issue Tracker](http://github.com/slackapi/node-slack-sdk/issues) for questions, feature requests, bug reports and
    general discussion related to this package. Try searching before you create a new issue.
  * [Email us](mailto:developers@slack.com) in Slack developer support: `developers@slack.com`
