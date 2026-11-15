# Conversations and files

Keep related work in one conversation and share only the files needed for it.

A conversation records messages and tool activity. The files an agent creates are separate deliverables on the machine running that conversation. Reopening a conversation restores its history; it does not make copies of every external file it referenced.

## Choose a working folder

For a folder-based coworker, select the project or workspace before starting. For everyday work, the session can have its own working area and access additional folders you share.

In **Settings → General → Files**, choose the default location for new conversations' working folders. Existing conversations keep their current folder. Changing this preference does not move old work.

## Give access to another folder

Open the conversation's **Access** section and choose **Give access to a folder…**. Select the folder and decide whether to enable **Allow writing (read-write)**. Additional folders are read-only unless you explicitly allow writing.

Use a small, task-specific folder when possible. Read-only access helps prevent edits through supported file tools; full command isolation depends on your [sandbox configuration](sandbox.md). A folder selection alone is not an operating-system sandbox.

For remote conversations, select a path on the remote machine. Local files don't appear there automatically.

## Attach context to a message

Use the composer's attachment control for supported images and files. PDF handling depends on the selected model. Some models read PDFs natively; others use the configured text-extraction or page-image fallback. Review any size or page-limit notice before sending.

Attaching a document can put its contents into model context. If you use a cloud provider, that context can leave the machine. For a large folder, letting the agent read selected files is often more useful than attaching everything.

## Find and check deliverables

Ask the coworker to name the destination file and folder. Use the file or preview controls shown in the app, or open the path in Finder or File Explorer. If a preview is unavailable, the saved file can still be inspected in its normal application.

Before sharing an output, check that the file exists, opens correctly and matches your request. For source-sensitive work, ask for filenames or references behind important claims.

## Continue or start fresh

Use the same conversation for follow-up edits and clarifications. Start a new one for a different task, coworker or machine. When context gets long, compaction can summarize older material; important decisions are worth keeping in an actual project file or brief.

For a practical example, [create and refine a meeting brief](first-task.md).
