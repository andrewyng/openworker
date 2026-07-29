# Slack Logger

[![codecov](https://codecov.io/gh/slackapi/node-slack-sdk/graph/badge.svg?token=OcQREPvC7r&flag=logger)](https://codecov.io/gh/slackapi/node-slack-sdk)

The `@slack/logger` package is intended to be used as a simple logging interface that supports verbosity levels.

## Requirements

This package supports Node v20 and higher. It's highly recommended to use [the latest LTS version of
node](https://github.com/nodejs/Release#release-schedule), and the documentation is written using syntax and features
from that version.

## Installation

```shell
$ npm install @slack/logger
```

## Usage

This package exports a `ConsoleLogger` class, a generic `Logger` interface and a `LogLevel` enum.
The source code is short (~150 lines of code), so check out `src/index.ts` for details, but the `ConsoleLogger` API
mimics the default node `console` API with three additions:

- `getLevel()`: returns the currently-specific `LogLevel` of the logger.
- `setLevel(LogLevel)`: sets the `LogLevel` of the logger.
- `setName(string)`: sets a prefix to display in logs. Useful if you have multiple loggers active.

### Example

This short snippet shows various `Logger` levels and customized labels:

```javascript
const { ConsoleLogger, LogLevel } = require("@slack/logger");

const logger = new ConsoleLogger();

logger.error("a problem happened");
logger.warn("might need attention");
logger.info("take note of this");
logger.debug("or dig into details");

logger.setName("HAL");
logger.setLevel(LogLevel.DEBUG);

logger.info("what an observation");
logger.debug("i am so interested");
```

When run, messages that match the following values are logged:

```txt
[ERROR]   a problem happened
[WARN]   might need attention
[INFO]   take note of this
[INFO]  HAL what an observation
[DEBUG]  HAL i am so interested
```

## Getting Help

If you get stuck, we're here to help. The following are the best ways to get assistance working through your issue:

- [Issue Tracker](http://github.com/slackapi/node-slack-sdk/issues) for questions, feature requests, bug reports and
  general discussion related to this package. Try searching before you create a new issue.
- [Email us](mailto:developers@slack.com) in Slack developer support: `developers@slack.com`
