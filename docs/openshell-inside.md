# Run OpenWorker inside an OpenShell sandbox

[OpenShell](https://github.com/NVIDIA/OpenShell) is NVIDIA's sandbox runtime for agents. This
page runs the OpenWorker CLI **inside** an OpenShell sandbox: the coworker, its tools and
the files it works on are all in the sandbox, and OpenShell decides what it can reach.

This is different from [OpenShell as the sandbox for the desktop app](openshell.md), where
OpenWorker runs on your computer and only its commands run in a sandbox.

Tested with OpenShell 0.1.2 on a Mac with Docker Desktop.

## What you need

- OpenShell 0.1.2, with its gateway running (`openshell status` says `Connected`).
- Docker. On a Mac, host networking turned on in Docker Desktop (**Settings ▸ Resources ▸
  Network ▸ Enable host networking**).
- A model API key from OpenAI or Anthropic.

## 1. Get the image

The image holds the OpenWorker CLI and common developer tools: Python, Node, git, curl,
ripgrep, `gh`, `uv`, `make` and a C compiler.

It is published as `ghcr.io/andrewyng/openworker:latest`, for Intel and ARM. There is
nothing to download first: OpenShell pulls it the first time you use it.

To build your own from a checkout of this repository, and use `openworker:local` in place
of the name below:

```bash
docker build -f packaging/openshell/Dockerfile -t openworker:local .
```

## 2. Give OpenShell your model key

OpenShell keeps the key. Inside the sandbox OpenWorker only sees a placeholder, and
OpenShell puts the real key on requests to the model provider and nowhere else.

For OpenAI:

```bash
openshell profile import -f packaging/openshell/providers/openworker-openai.yaml
export OPENAI_API_KEY=...        # your key
openshell provider create --name openworker-openai --type openworker-openai --from-existing
```

For Anthropic, use `openworker-anthropic.yaml`, `ANTHROPIC_API_KEY` and the name
`openworker-anthropic` in the same three commands.

You do this once. The provider stays on your gateway.

## 3. Run a task

One command starts a sandbox, runs the task and removes the sandbox:

```bash
openshell sandbox create --from ghcr.io/andrewyng/openworker:latest \
    --provider openworker-openai --no-keep \
    -- openworker run --approval-mode bypass-approvals --prompt "Say hello"
```

The answer is printed in your terminal. Everything after `--` is the normal
[`openworker run`](headless.md) command.

`--approval-mode bypass-approvals` lets the coworker act without asking, which is reasonable
here because the sandbox is what contains it. The other modes are on the
[`openworker run`](headless.md) page.

## 4. Work on your own files

A sandbox has its own files. Bring a project in, run the task, and take the result out:

```bash
# A sandbox that stays up
openshell sandbox create --name work --from ghcr.io/andrewyng/openworker:latest \
    --provider openworker-openai --detach -- sleep infinity

# Copy the project in. It lands at /sandbox/my-project
openshell sandbox upload work ./my-project /sandbox

# Run a task in it
openshell sandbox exec -n work --workdir /sandbox/my-project \
    -- openworker run --approval-mode bypass-approvals --prompt "Fix the failing test"

# Copy a file, or the whole folder, back out
openshell sandbox download work /sandbox/my-project ./my-project-result

# Remove the sandbox when you are done
openshell sandbox delete work
```

## Choose a coworker

It is the same image for every coworker. Name the one you want with `--coworker`:

```bash
openshell sandbox exec -n work --workdir /sandbox/my-project \
    -- openworker run --coworker code --prompt "Fix the failing test"
```

`cowork` is the default. An unknown id stops the run and lists the ids the image has.

## Choose a model

Add `--model provider:model`, for example `--model openai:gpt-5.6-sol`. The provider you
attached has to match: a sandbox with only the OpenAI provider cannot reach Anthropic.

## Sites

A sandbox reaches only what OpenShell's policy allows. With the provider attached, that is
the model API and nothing else. A command or web fetch to any other site fails, and the
coworker is told that the sandbox blocked it.

To allow a site, on your computer:

```bash
openshell policy update work --add-endpoint pypi.org:443 --binary '/**' --wait
```

`--binary` says which programs may use the site. `'/**'` means any program in the sandbox;
name one, such as `/usr/bin/curl`, to keep it to that program. Do not leave `--binary` out:
OpenShell then adds a rule that lets no program through. The change reaches the running
sandbox in a few seconds, with no restart.

When a task needs a blocked site and you are at the terminal, the coworker asks you and
waits. The question names the site and gives this command. Run it in another terminal,
with the sandbox's name from `openshell sandbox list`, then answer that you allowed it and
the coworker tries again. Answer "leave it blocked" and it finishes without the site.

With `--auto-answer`, or with no terminal, nobody is there to ask. The coworker finishes
what it can and its answer says which site was blocked. A run started with `--no-keep`
(step 3) is gone when it ends, so allow the site on a sandbox that stays up (step 4) and
run the task again.

OpenShell can also draft these rules from the requests it blocked, for you to approve. That
is its policy advisor; see OpenShell's
[guide](https://docs.nvidia.com/openshell/latest/how-it-works/policies/advisor).

OpenWorker's own `--allow-site` has no effect inside OpenShell: the sandbox's policy is
OpenShell's to change, not OpenWorker's.

## GitHub

Attach OpenShell's GitHub provider and the coworker can use `gh` and `git`. OpenShell puts
your token on requests from those two programs only. See OpenShell's guide for
[GitHub push access](https://docs.nvidia.com/openshell/latest/tutorials/github-push-access).

## What is different inside a sandbox

- Connectors from the desktop app are not there. The sandbox starts with no settings.
- Each run is saved in the sandbox, under `/sandbox/.config/coworker/sessions/`. It is gone
  when the sandbox is deleted, so download what you want to keep.
- A program the coworker starts can use the model key in the same way OpenWorker can. It
  cannot read the key.
