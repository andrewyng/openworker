# Create your first meeting brief

Turn three short meeting notes into a brief you can review, save and refine.

You'll ask OpenWorker to identify decisions, open questions and next steps, then save the result as `brief.md`. This task uses local files and your chosen model. You don't need to connect Slack, email or any other service.

## Set up your model

Download and open OpenWorker for [macOS](https://download.openworker.com/mac) or [Windows](https://download.openworker.com/windows).

In the initial setup, choose a model provider and connect it. If you've already opened the app, use **Settings → Models & Keys**. You can use your own cloud-provider key or a local model server, such as Ollama. A local model needs to support tool calling for this exercise.

Choose a connected model in the message composer. You can skip connecting other apps and continue without signing into an OpenWorker account for this task.

## Open the sample folder

[Download the sample meeting notes](../samples/meeting-notes.zip) and extract the ZIP on your computer. Keep the three Markdown files together in the `meeting-notes` folder. They're fictional notes created for this guide.

| File | What's in it |
|---|---|
| `01-kickoff.md` | The launch goal, scope and initial decisions |
| `02-design-review.md` | Design feedback, an accessibility concern and owners |
| `03-launch-check.md` | Readiness checks, a remaining question and next steps |

Start a **New session** with the default **OpenWorker** coworker. In the start view, select **Analyze the files in a directory**, then pick your extracted `meeting-notes` folder. Enable **Allow writing (read-write)** and choose **Add** so OpenWorker can save the brief there.

The shortcut may prefill a suggested message. Replace it with the request below. Before sending, select **Ask for approval** from the mode menu in the composer.

<details>
<summary>Already in a conversation, or don't see the shortcut?</summary>

Open the session's **Access** section, choose **Give access to a folder…**, select `meeting-notes`, enable **Allow writing (read-write)** and choose **Add**. Return to the composer and select **Ask for approval**.

Use this sample folder for the exercise so the agent has the context it needs without access to unrelated documents.

</details>

## Ask for the brief

Paste this request and send it:

```text
Read the three meeting notes in the meeting-notes folder I shared.

Create a concise brief with:
- the launch goal and decisions made;
- open questions and risks;
- next steps, with an owner and due date when the notes provide one.

Say when an owner or date is missing. Include the source filename for
each decision and next step so I can check it.

Save the result as brief.md in that same meeting-notes folder. Leave
the original notes unchanged. Ask before writing the brief, and do not
send it anywhere or connect to any other service.
```

The useful parts of this request are the **outcome**, the **source material**, the **destination** and the **boundaries**. Use the same pattern for your own tasks.

## Review the proposed action

Follow the tool activity as OpenWorker reads the notes. Reading may not need an approval card. If it asks a question, answer it in the conversation.

When a write or command needs approval, check what it will do. For this task, the intended change is a new `brief.md` inside your extracted `meeting-notes` folder. Approve the action if its path and contents match your request. If it proposes an unrelated change, decline it and explain what should change.

If no card appears, check the current mode and any saved permissions. Asking for approval in a prompt expresses your preference; the app's mode and permission rules govern how actions are reviewed.

## Check the finished file

Open `brief.md` in your `meeting-notes` folder using Finder or File Explorer. You can read a Markdown file in a text editor. A message saying “saved” is not a substitute for checking the file.

The brief should capture these details from the sample notes:

- The first release is a **read-only preview**; editing is deferred.
- **Priya** owns the keyboard-navigation fix, due **Thursday**.
- **Sam** owns the smoke test, due **Friday**.
- The support handoff still needs an owner, and the launch date is not yet confirmed.

Check the source filenames and make sure the original notes are unchanged. Wording and formatting will vary with the model you use.

## Refine it in the same session

Continue with a follow-up:

```text
Make the brief easier to scan. Put the next steps in a table, and mark
anything without an owner or due date as “Needs a decision”. Update
only brief.md in the meeting-notes folder, and ask before changing it.
```

Review the proposed edit and reopen the file. The session keeps the earlier conversation, so you can refine the work without repeating the full request.

<details>
<summary>If the task gets stuck</summary>

- **Can't send a message:** check that a provider is connected and a model is selected.
- **The notes can't be read:** check that the ZIP was extracted and the extracted folder appears in the session's Access section.
- **Writing is refused:** check that this folder has read-write access and the mode is Ask for approval, rather than Discuss or Plan.
- **The reply appears only in chat:** ask OpenWorker to save `brief.md` in the shared `meeting-notes` folder, then look for an approval request.
- **A local model describes tools but doesn't use them:** choose a tool-capable model and check its server configuration.

If you report a problem, include your operating system, app version, model/provider and a redacted error message. Never include API keys or private note contents.

</details>
