## ADDED Requirements

### Requirement: Matrix target encoding

Outbound tools (`send_message`, `send_file`) and standing approval grants SHALL address Matrix destinations using the format `matrix/<urlsafe_base64(room_id)>[/thread/<urlsafe_base64(thread_root_event_id)>]`. Encoding SHALL use URL-safe base64 without padding. Decoding SHALL recover the full Matrix room ID and optional thread root event ID.

#### Scenario: Parse room-only target

- **WHEN** the target is `matrix/<encoded_room>` where decoded room id is `!abc:example.org`
- **THEN** `parse_target` returns platform `matrix`, chat_id `!abc:example.org`, and thread_id `None`

#### Scenario: Parse threaded target

- **WHEN** the target includes `/thread/<encoded_event>`
- **THEN** `parse_target` returns the decoded thread root event id as `thread_id`

#### Scenario: Invalid matrix target rejected

- **WHEN** the target starts with `matrix/` but base64 decoding fails
- **THEN** parsing SHALL raise a clear validation error

### Requirement: Matrix targets in inbound reply handles

Inbound Matrix messages SHALL expose reply handles using the same encoding in `SessionSource.target` and in agent-facing tagged text, so agents can pass the handle back to `send_message` unchanged.

#### Scenario: Inbound tagged text includes matrix target

- **WHEN** a message arrives from room `!ops:example.org` in thread rooted at `$event123`
- **THEN** the tagged inbound text includes a reply handle matching the encoded matrix target format

### Requirement: Slack and Telegram targets unchanged

Existing `platform:chat_id[:thread]` parsing for non-matrix platforms SHALL remain backward compatible.

#### Scenario: Slack target still parses

- **WHEN** the target is `slack:C0123456789:1700000000.000100`
- **THEN** parsing returns platform slack with the expected chat_id and thread_id
