# Bolt <img src="https://raw.githubusercontent.com/slackapi/bolt-js/main/docs/img/bolt.svg" alt="Bolt logo" width="32"/> for JavaScript

[![codecov](https://codecov.io/gh/slackapi/bolt-js/branch/main/graph/badge.svg?token=x4oCgiexvp)](https://codecov.io/gh/slackapi/bolt-js)
[![Node.js CI](https://github.com/slackapi/bolt-js/actions/workflows/ci-build.yml/badge.svg)](https://github.com/slackapi/bolt-js/actions/workflows/ci-build.yml)

A JavaScript framework to build Slack apps in a flash with the latest platform features. Read the [getting started guide](https://docs.slack.dev/tools/bolt-js/getting-started) to set-up and run your first Bolt app.

Read [the documentation](https://docs.slack.dev/tools/bolt-js) to explore the basic and advanced concepts of Bolt for JavaScript.

## Setup

```bash
npm install @slack/bolt
```

## Initialization

Create an app by calling the constructor, which is a top-level export.

```js
import { App } from '@slack/bolt';

const app = new App({
  signingSecret: process.env.SLACK_SIGNING_SECRET,
  token: process.env.SLACK_BOT_TOKEN,
});

/* Add functionality here */

(async () => {
  // Start the app
  await app.start(process.env.PORT || 3000);

  app.logger.info('⚡️ Bolt app is running!');
})();
```

## Listening for events

The Slack **Request URL** for a Bolt app must have the path set to `/slack/events`.  
For example: `https://my-slack-app.example.com/slack/events`.  
Otherwise, all incoming requests from Slack won't be handled.

Apps typically react to a collection of incoming events, which can correspond [Events API events](https://docs.slack.dev/apis/events-api), [actions](https://docs.slack.dev/interactivity/handling-user-interaction), [shortcuts](https://docs.slack.dev/interactivity/implementing-shortcuts), [slash commands](https://docs.slack.dev/interactivity/implementing-slash-commands) or [options requests](https://docs.slack.dev/reference/block-kit/block-elements/select-menu-element#external-data-source). For each type of
request, there's a method to build a listener function.

```js
// Listen for an action from a Block Kit element (buttons, select menus, date pickers, etc)
app.action(actionId, fn);

// Listen for dialog submissions
app.action({ callback_id: callbackId }, fn);

// Listen for slash commands
app.command(commandName, fn);

// Listen for an event from the Events API
app.event(eventType, fn);

// Listen for a custom step execution from a workflow
app.function(callbackId, fn)

// Convenience method to listen to only `message` events using a string or RegExp
app.message([pattern ,] fn);

// Listen for options requests (from select menus with an external data source)
app.options(actionId, fn);

// Listen for a global or message shortcuts
app.shortcut(callbackId, fn);

// Listen for view_submission modal events
app.view(callbackId, fn);

```

## Making things happen

Most of the app's functionality will be inside listener functions (the `fn` parameters above). These functions are called with a set of arguments.

| Argument  | Description  |
| :---: | :--- |
| `payload` | Contents of the incoming event. The payload structure depends on the listener. For example, for an Events API event, `payload` will be the [event type structure](https://docs.slack.dev/apis/events-api#event-type-structure). For a block action, it will be the action from within the `actions` array. The `payload` object is also accessible via the alias corresponding to the listener (`message`, `event`, `action`, `shortcut`, `view`, `command`, or `options`). For example, if you were building a `message()` listener, you could use the `payload` and `message` arguments interchangeably. **An easy way to understand what's in a payload is to log it**, or use TypeScript. |
| `say` | Function to send a message to the channel associated with the incoming event. This argument is only available when the listener is triggered for events that contain a `channel_id` (the most common being `message` events). `say` accepts simple strings (for plain-text messages) and objects (for messages containing blocks). `say` returns a promise that will resolve with a [`chat.postMessage` response](https://docs.slack.dev/reference/methods/chat.postMessage).
| `ack` | Function that **must** be called to acknowledge that an incoming event was received by your app. `ack` exists for all actions, shortcuts, view, slash command and options requests. `ack` returns a promise that resolves when complete. Read more in [Acknowledging events](#acknowledging-events)
| `client` | Web API client that uses the token associated with that event. For single-workspace installations, the token is provided to the constructor. For multi-workspace installations, the token is returned by the `authorize` function.
| `respond` | Function that responds to an incoming event **if** it contains a `response_url` (actions, shortcuts, view submissions, and slash commands). `respond` returns a promise that resolves with the results of responding using the `response_url`.
| `context` | Event context. This object contains data about the event and the app, such as the `botId`. Middleware can add additional context before the event is passed to listeners.
| `body` | Object that contains the entire body of the request (superset of `payload`). Some accessory data is only available outside of the payload (such as `trigger_id` and `authorizations`).
| `complete` | Function used to signal the successful completion of a custom step execution. This tells Slack to proceed with the next steps in the workflow. This argument is only available with the `.function` and `.action` listener when handling custom workflow step executions.
| `fail` | Function used to signal that a custom step failed to complete. This tells Slack to stop the workflow execution. This argument is only available with the `.function` and `.action` listener when handling custom workflow step executions.


The arguments are grouped into properties of one object, so that it's easier to pick just the ones your listener needs (using
[object destructuring](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Destructuring_assignment#Unpacking_fields_from_objects_passed_as_function_parameter)). Here is an example where the app sends a simple response, so there's no need for most of these arguments:

```js
// Reverse all messages the app can hear
app.message(async ({ message, say }) => {
  // Filter out message events with subtypes (see https://docs.slack.dev/reference/events/message)
  if (message.subtype === undefined || message.subtype === 'bot_message') {
    const reversedText = [...message.text].reverse().join("");
    await say(reversedText);
  }
});
```

### Calling the Web API

In addition to the [`client` property passed to listeners](#making-things-happen), each app has a top-level `client` that can be used to call methods. Unlike the `client` passed to listeners, the top-level client must be passed a `token`. [Read the documentation](https://docs.slack.dev/tools/bolt-js/concepts#web-api) for more details.

### Acknowledging events

Some types of events need to be acknowledged in order to ensure a consistent user experience inside the Slack client (web, mobile, and desktop apps). This includes all action, shortcut, view, command, and options requests. Listeners for these events need to call the `ack()` function, which is passed in as an argument.

In general, the Slack platform expects an acknowledgement within 3 seconds, so listeners should call this function as soon as possible.

Depending on the type of incoming event a listener is meant for, `ack()` should be called with a parameter:

*  Block actions, global shortcuts, and message shortcuts: Call `ack()` with no parameters.

* View submissions: Call `ack()` with no parameters or with a [response action](https://docs.slack.dev/surfaces/modals#updating-response).

*  Options requests: Call `ack()` with an object containing the options for the user to see.

*  Legacy message button clicks, menu selections, and slash commands: Either call `ack()` with no parameters, a `string` to to update the message with a simple message, or an `object` to replace it with a complex message. Replacing the message to remove the interactive elements is a best practice for any action that should only be performed once.

* Events API events **do not** need an `ack()` function since they are automatically acknowledged by your app.

## Getting Help

[The documentation](https://docs.slack.dev/tools/bolt-js) has more information on basic and advanced concepts for Bolt for JavaScript.

If you otherwise get stuck, we're here to help. The following are the best ways to get assistance working through your issue:

  * [Issue Tracker](http://github.com/slackapi/bolt-js/issues) for questions, bug reports, feature requests, and general discussion related to Bolt for JavaScript. Try searching for an existing issue before creating a new one.
  * [Email](mailto:support@slack.com) our developer support team: `support@slack.com`
## Contributing

We welcome contributions from everyone! Please check out our
[Contributor's Guide](.github/contributing.md) for how to contribute in a
helpful and collaborative way.
