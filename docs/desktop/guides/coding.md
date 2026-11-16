# Work with the coding coworker

Ask Code to explore a repository, implement a change and show how it was verified.

Enable **Code** in **Settings → Coworkers**, then choose it for a new conversation. Select the repository folder and a tool-capable model. Start with **Ask for approval** so you can review commands and edits as you learn the workflow.

## Start with a bounded change

Tell Code what should change and what a successful result looks like:

```text
In this repository, add validation for an empty display name. Follow
the existing patterns and add a regression test. Run the relevant
tests, then explain the changed files and any remaining failures.
Don't push, open a pull request or deploy anything.
```

For a larger request, ask it to inspect the project and propose a plan before making changes. A good task includes the behavior you want, constraints, a verification method and whether publishing is authorized.

## Follow the work

Review tool activity for file changes, searches, commands and test results. Dependencies may need to be installed on the machine running the session. A command's successful exit alone doesn't prove the application behaves correctly; ask for targeted tests or a preview when appropriate.

If the coworker delegates, inspect the relevant worker conversation and the lead's final verification. Several workers sharing a machine still need to coordinate directories, branches and server ports.

## Know which checkout is being edited

Check the branch and working directory before and after the task. Current Code workflows support a branch and Git worktree per task. When your build exposes **Feature worktrees** in Settings, you can configure their location, branch prefix and setup command. Older builds may use the supplied checkout directly; confirm the actual path rather than assuming worktree isolation.

A worktree is a separate checkout, not a security sandbox. It may share Git metadata with the main repository. Use a [sandbox](sandbox.md) when you need execution restrictions.

## Review before publishing

Ask for the diff, test results and any incomplete work. A local edit or commit is not a pushed branch. GitHub actions such as opening a PR need a configured connector or appropriately shared command-line login, and authorization for the target repository.

Don't paste access tokens into the conversation. If commands need a login inside a sandbox, read [native sandbox credential sharing](native-sandbox.md#share-logins-deliberately). A GitHub connector uses a different execution path from `git` or `gh` inside a sandbox.

## Work on a remote repository

For a conversation on another machine, the repository and dependencies must exist there. A path on your laptop doesn't point to the same files on the VM. Choose the machine first, then select its folder. See [Choose where work runs](run-location.md).
