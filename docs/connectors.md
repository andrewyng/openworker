# Messaging Connectors

OpenWorker can receive and respond to messages from messaging platforms (Slack, Telegram) through two-way connectors.

## How connectors work

When you enable a messaging connector, OpenWorker starts a **listener** that monitors inbound messages on that platform. When a message arrives:

1. The listener checks the **allow-list** to decide if the sender is authorized
2. If authorized, the message is routed to the agent for processing
3. The agent's response is sent back as a reply in the same thread/channel

Unauthorized messages are **parked** on the connector's page inside the app rather than lost — you can review and respond to them later.

## The allow-list (deny-by-default)

The allow-list is the inbound security guard. By default it is **empty**, meaning no one can reach the agent through a connector.

### Adding yourself

1. Open OpenWorker and go to the connector's page
2. Find your user ID on the platform and add it to the allowed list
3. Once added, your messages will be routed to the agent

You can also set `allow_all` to true if you want to accept messages from anyone (not recommended for production use).

### Where it's checked

See `coworker/connectors/config.py`:

```python
def is_authorized(settings: ConnectorSettings, source: SessionSource) -> bool:
    ...
```

## Telegram setup

1. Create a bot with [BotFather](https://t.me/botfather) on Telegram
2. Copy the bot token
3. In OpenWorker, go to Integrations > Telegram and paste the token
4. Enable the connector
5. Add your Telegram user ID to the allow-list

### Telegram privacy mode

By default, BotFather enables **privacy mode** for bots. This means:

- **Direct messages (DMs):** the bot receives all messages — this is the most reliable way to test
- **Groups:** the bot only receives messages that start with a command (`/`), reply to its messages, or mention it by username

If the bot isn't responding in a group, check:
1. Is privacy mode enabled? Use BotFather to disable it if needed (`/setprivacy`)
2. Are you mentioning the bot correctly? (`@YourBotName your message`)

Always test with a direct message first before testing in groups.

## Slack setup

1. Create a Slack app in the [Slack API dashboard](https://api.slack.com/apps)
2. Add the required bot token scopes
3. Install the app to your workspace
4. Copy the bot token and paste it in OpenWorker (Integrations > Slack)
5. Enable the connector
6. Add your Slack user/member ID to the allow-list

### Socket Mode vs. managed relay

Slack supports two connection modes:

- **Socket Mode:** runs entirely on your machine. Set up a Slack app with Socket Mode enabled and paste the app-level token.
- **Managed relay (managed):** uses OpenWorker's OAuth relay for simpler setup. Requires signing in to your OpenWorker account.

## Running from source

If you're running OpenWorker from source, the messaging extras are needed:

```bash
# The dev setup script installs the [messaging] extra automatically:
bash packaging/setup_dev_env.sh

# If you're setting up manually, install with messaging support:
pip install -e ".[messaging]"
```

Without the `[messaging]` extra, the listener will fail to start (missing `slack-bolt`, `python-telegram-bot`, etc.).

## Troubleshooting: "Connected but not receiving"

If the connector shows as connected but messages aren't reaching the agent:

1. **Check the allow-list** — your user ID must be added (empty allow-list = nobody gets through)
2. **Check the connector page** — unauthorized messages are parked there
3. **Check the listener status** — make sure the listener is actually running (a failed start may still show as "Live")
4. **Check the install extras** — `[messaging]` must be installed when running from source
5. **Test with a direct message** — avoids platform-specific group routing issues (Telegram privacy mode, Slack channel visibility)
