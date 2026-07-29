# Slack Web API

[![codecov](https://codecov.io/gh/slackapi/node-slack-sdk/graph/badge.svg?token=OcQREPvC7r&flag=web-api)](https://codecov.io/gh/slackapi/node-slack-sdk)

The `@slack/web-api` package contains a simple, convenient, and configurable HTTP client for making requests to Slack's
[Web API](https://docs.slack.dev/apis/web-api). Use it in your app to call any of the over 130
[methods](https://docs.slack.dev/reference/methods), and let it handle formatting, queuing, retrying, pagination, and more.

## Requirements

This package supports Node v20 and higher. It's highly recommended to use [the latest LTS version of
node](https://github.com/nodejs/Release#release-schedule), and the documentation is written using syntax and features
from that version.

## Installation

```shell
$ npm install @slack/web-api
```

<!-- START: Remove before copying into the docs directory -->

## Usage

These examples show the most common features of the `WebClient`. You'll find even more extensive [documentation on the
package's website](https://docs.slack.dev/tools/node-slack-sdk/web-api/).

<!-- END: Remove before copying into the docs directory -->

---

### Initialize the client

The package exports a `WebClient` class. All you need to do is instantiate it, and you're ready to go. You'll typically
initialize it with a `token`, so that you don't have to provide the token each time you call a method. A token usually
begins with `xoxb` or `xoxp`. You get them from each workspace an app is installed onto. The app configuration pages
help you get your first token for your development workspace.

```javascript
const { WebClient } = require('@slack/web-api');

// Read a token from the environment variables
const token = process.env.SLACK_TOKEN;

// Initialize
const web = new WebClient(token);
```

<details>
<summary markdown="span">
<strong><i>Initializing without a token</i></strong>
</summary>

Alternatively, you can create a client without a token, and use it with multiple workspaces as long as you supply a
`token` when you call a method.

```javascript
const { WebClient } = require('@slack/web-api');

// Initialize a single instance for the whole app
const web = new WebClient();

// Find a token in storage (database) before making an API method call
(async () => {
  // Some fictitious database
  const token = await db.findTokenByTeam(teamId, enterpriseId)

  // Call the method
  const result = web.auth.test({ token });
})();
```
</details>

---

### Call a method

The client instance has a named method for each of the public methods in the Web API. The most popular one is
called `chat.postMessage`, and it's used to send a message to a conversation. For every method, you pass arguments as
properties of an options object. This helps with the readability of your code since every argument has a name. All
named methods return a `Promise` which resolves with the response data or rejects with an error.

```javascript
// Given some known conversation ID (representing a public channel, private channel, DM or group DM)
const conversationId = '...';

(async () => {

  // Post a message to the channel, and await the result.
  // Find more arguments and details of the response: https://docs.slack.dev/reference/methods/chat.postMessage
  const result = await web.chat.postMessage({
    text: 'Hello world!',
    channel: conversationId,
  });

  // The result contains an identifier for the message, `ts`.
  console.log(`Successfully send message ${result.ts} in conversation ${conversationId}`);
})();
```

**Tip**: If you're using an editor that supports TypeScript, even if you're not using TypeScript to write your code,
you'll get hints for all the arguments each method supports. This helps you save time by reducing the number of
times you need to pop out to a webpage to check the reference. There's more information about [using
TypeScript](https://docs.slack.dev/tools/node-slack-sdk/typescript/) with this package in the documentation website.

**Tip**: Use the [Block Kit Builder](https://app.slack.com/block-kit-builder) for a playground
where you can prototype your message's look and feel.

<details>
<summary markdown="span">
<strong><i>Using a dynamic method name</i></strong>
</summary>

If you want to provide the method name as a string so that you can decide which method to call dynamically or to call
a method that might not be available in your version of the client, use the `WebClient.apiCall(methodName, [options])`
method. The API method call above can also be written as follows:

```javascript
const conversationId = '...';
(async () => {

  // Using apiCall() allows the app to call any method and to do it programmatically
  const response = await web.apiCall('chat.postMessage', {
    text: 'Hello world!',
    channel: conversationId,
  });
})();
```
</details>

---

### Handle errors

Errors can happen for many reasons: maybe the token doesn't have the proper [scopes](https://docs.slack.dev/reference/scopes) to
call a method, maybe it's been revoked by a user, or maybe you just used a bad argument. In these cases, the returned
`Promise` will reject with an `Error`. You should catch the error and use the information it contains to decide how your
app can proceed.

Each error contains a `code` property, which you can check against the `ErrorCode` export to understand the kind of
error you're dealing with. For example, when Slack responds to your app with an error, that is an
`ErrorCode.PlatformError`. These types of errors provide Slack's response body as the `data` property.

```javascript
// Import ErrorCode from the package
const { WebClient, ErrorCode } = require('@slack/web-api');

(async () => {

  try {
    // This method call should fail because we're giving it a bogus user ID to lookup.
    const response = await web.users.info({ user: '...' });
  } catch (error) {
    // Check the code property, and when it's a PlatformError, log the whole response.
    if (error.code === ErrorCode.PlatformError) {
      console.log(error.data);
    } else {
      // Some other error, oh no!
      console.log('Well, that was unexpected.');
    }
  }
})();
```

<details>
<summary markdown="span">
<strong><i>More error types</i></strong>
</summary>

There are a few more types of errors that you might encounter, each with one of these `code`s:

* `ErrorCode.RequestError`: A request could not be sent. A common reason for this is that your network connection is
  not available, or `api.slack.com` could not be reached. This error has an `original` property with more details.

* `ErrorCode.RateLimitedError`: The Web API cannot fulfill the API method call because your app has made too many
  requests too quickly. This error has a `retryAfter` property with the number of seconds you should wait before trying
  again. See [the documentation on rate limit handling](https://docs.slack.dev/tools/node-slack-sdk/web-api/#rate-limits) to
  understand how the client will automatically deal with these problems for you.

* `ErrorCode.HTTPError`: The HTTP response contained an unfamiliar status code. The Web API only responds with `200`
  (yes, even for errors) or `429` (rate limiting). If you receive this error, it's likely due to a problem with a proxy,
  a custom TLS configuration, or a custom API URL. This error has the `statusCode`, `statusMessage`, `headers`, and
  `body` properties containing more details.
</details>

---

### Pagination

[Many of the Web API's methods](https://docs.slack.dev/apis/web-api/pagination) return
lists of objects, and are known to be **cursor-paginated**. The result of calling these methods will contain a part of
the list, or a page, and also provide you with information on how to continue to the next page on a subsequent API call.
Instead of calling many times manually, the `WebClient` can manage to get each page, allowing you to determine when to
stop, and help you process the results.

The process of retrieving multiple pages from Slack's API can be described as **asynchronous iteration**, which means
you're processing items in a collection, but getting each item is an asynchronous operation. Fortunately, JavaScript
has this concept built-in, and in newer versions of the language there's a syntax to make it even simpler:
[`for await...of`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/for-await...of).

```javascript
(async () => {
  let result;

  // Async iteration is similar to a simple for loop.
  // Use only the first two parameters to get an async iterator.
  for await (const page of web.paginate('something.list', { name: 'value' })) {
    // You can inspect each page, find your result, and stop the loop with a `break` statement
    if (containsTheThing(page.something)) {
      result = page.something.thing;
      break;
    }
  }
});
```

The `for await...of` syntax is available in Node v10.0.0 and above. If you're using an older version of Node, see
functional iteration below.

<details>
<summary markdown="span">
<strong><i>Using functional iteration</i></strong>
</summary>

The `.paginate()` method can accept up to two additional parameters. The third parameter, `stopFn`, is a function that
is called once for each page of the result, and should return `true` when the app no longer needs to get another page.
The fourth parameter is `reducerFn`, which is a function that gets called once for each page of the result, but can
be used to aggregate a result. The value it returns is used to call it the next time as the `accumulator`. The first
time it gets called, the `accumulator` is undefined.

```javascript
(async () => {

  // The first two parameters are the method name and the options object.
  const done = await web.paginate('something.list', { name: 'value' },
    // The third is a function that receives each page and should return true when the next page isn't needed.
    (page) => { /* ... */ },
    // The fourth is a reducer function, similar to the callback parameter of Array.prototype.reduce().
    // https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/Reduce
    // The accumulator is initialized to undefined.
    (accumulator, page, index) => { /* ... */ },
  );
});
```

The returned value is a `Promise`, but what it resolves to depends on whether or not you include the fourth (optional)
parameter. If you don't include it, the resolved value is always `undefined`. In this case, it's used for control flow
purposes (resuming the rest of your program), and the function in the third parameter is used to capture a result. If
you do include the fourth parameter, then the resolved value is the value of the `accumulator`. This is a familiar
pattern for people that use _functional programming_.

</details>

---

### Logging

The `WebClient` will log interesting information to the console by default. You can use the `logLevel` to decide how
much information, or how interesting the information needs to be, in order for it to be output. There are a few possible
log levels, which you can find in the `LogLevel` export. By default, the value is set to `LogLevel.INFO`. While you're
in development, it's sometimes helpful to set this to the most verbose: `LogLevel.DEBUG`.

```javascript
// Import LogLevel from the package
const { WebClient, LogLevel } = require('@slack/web-api');

// Log level is one of the options you can set in the constructor
const web = new WebClient(token, {
  logLevel: LogLevel.DEBUG,
});
```

All the log levels, in order of most to least information, are: `DEBUG`, `INFO`, `WARN`, and `ERROR`.

<details>
<summary markdown="span">
<strong><i>Sending log output somewhere besides the console</i></strong>
</summary>

You can also choose to have logs sent to a custom logger using the `logger` option. A custom logger needs to implement
specific methods (known as the `Logger` interface):

| Method       | Parameters        | Return type |
|--------------|-------------------|-------------|
| `setLevel()` | `level: LogLevel` | `void`      |
| `setName()`  | `name: string`    | `void`      |
| `debug()`    | `...msgs: any[]`  | `void`      |
| `info()`     | `...msgs: any[]`  | `void`      |
| `warn()`     | `...msgs: any[]`  | `void`      |
| `error()`    | `...msgs: any[]`  | `void`      |

A very simple custom logger might ignore the name and level, and write all messages to a file.

```javascript
const { createWriteStream } = require('fs');
const logWritable = createWriteStream('/var/my_log_file'); // Not shown: close this stream

const web = new WebClient(token, {
  // Creating a logger as a literal object. It's more likely that you'd create a class.
  logger: {
    debug(...msgs): { logWritable.write('debug: ' + JSON.stringify(msgs)); },
    info(...msgs): { logWritable.write('info: ' + JSON.stringify(msgs)); },
    warn(...msgs): { logWritable.write('warn: ' + JSON.stringify(msgs)); },
    error(...msgs): { logWritable.write('error: ' + JSON.stringify(msgs)); },
    setLevel(): { },
    setName(): { },
  },
});
```
</details>

---

### Automatic retries

In production systems, you want your app to be resilient to short hiccups and temporary outages. Solving for this
problem usually involves building a queuing system that handles retrying failed tasks. The `WebClient` comes with this
queuing system out of the box, and it's on by default! The client will retry a failed API method call up to 10 times,
spaced out over about 30 minutes. If the request doesn't succeed within that time, then the returned `Promise` will reject.
You can observe each of the retries in your logs by [setting the log level to DEBUG](#logging). Try running the
following code with your network disconnected, and then re-connect after you see a couple of log messages:

```javascript
const { WebClient, LogLevel } = require('@slack/web-api');

const web = new WebClient('bogus token');

(async () => {
  await web.auth.test();

  console.log('Done!');
})();
```

Shortly after re-connecting your network, you should see the `Done!` message. Did you notice the program doesn't use a
valid token? The client is doing something clever and helpful here. It knows the difference between an error such as not
being able to reach `api.slack.com` and an error in the response from Slack about an invalid token. The former is
something that can be resolved with a retry, so it was retried. The invalid token error means that the call isn't going
to succeed until your app does something differently, so it stops attempting retries.

You might not think 10 reties in 30 minutes is a good policy for your app. No problem, you can set the `retryConfig` to
one that works better for you. The `retryPolicies` export contains a few well known options, and you can always write
your own.

```javascript
const { WebClient, retryPolicies } = require('@slack/web-api');

const web = new WebClient(token, {
  retryConfig: retryPolicies.fiveRetriesInFiveMinutes,
});
```

Here are some other values that you might want to use for `retryConfig`:

| `retryConfig`                                  | Description                     |
|------------------------------------------------|---------------------------------|
| `retryPolicies.tenRetriesInAboutThirtyMinutes` | (default)                       |
| `retryPolicies.fiveRetriesInFiveMinutes`       | Five attempts in five minutes   |
| `retryPolicies.rapidRetryPolicy`               | Used to keep tests running fast |
| `{ retries: 0 }`                               | No retries ([other options](https://github.com/tim-kos/node-retry#retryoperationoptions)) |

**Note**: If an API call results in a rate limit being exceeded, you might still notice the client automatically
retrying the API call. If you'd like to opt out of that behavior, set the `rejectRateLimitedCalls` option to `true`.

---

### More

The [documentation website](https://docs.slack.dev/tools/node-slack-sdk/web-api/) has information about these additional features of
the `WebClient`:

*  Upload a file with a `Buffer` or a `ReadableStream`.
*  Using a custom agent for proxying
*  Rate limit handling
*  Request concurrency
*  Custom TLS configuration
*  Custom API URL
*  Exchange an OAuth grant for a token

---

## Getting Help

If you get stuck, we're here to help. The following are the best ways to get assistance working through your issue:

  * [Issue Tracker](http://github.com/slackapi/node-slack-sdk/issues) for questions, feature requests, bug reports and
    general discussion related to these packages. Try searching before you create a new issue.
  * [Email us](mailto:feedback@slack.com): `feedback@slack.com`
  * [Community Slack](https://community.slack.com/): a Slack community for developers
    building all kinds of Slack apps. You can find the maintainers and users of these packages in **#lang-javascript**.
