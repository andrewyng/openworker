# One cloud runtime per bot

This deployment gives each bot a separate Linux container, private persistent
volume, browser profile, identity, provider keys, files and conversations. Run it
on an existing cloud Docker host, or put each deployment on its own cloud VM when
you need VM-level separation. Containers on one host share the host kernel.

The runtime dials out to a controller; no API, database, Docker socket or browser
debugging port is exposed. The controller must be reachable at an HTTPS origin
and support the existing OpenWorker device enrollment flow. Infrastructure and
model-provider costs are charged by your providers. These commands do not create
a cloud account or buy a VM.

## Build and deploy

From this checkout, build the runtime image on the intended host architecture:

```sh
docker build -f deploy/bots/Dockerfile -t openworker-bot-runtime:local .
python deploy/bots/create.py --name researcher \
  --controller https://your-controller.example \
  --image openworker-bot-runtime:local --output ./researcher-runtime
docker compose -f researcher-runtime/compose.json up -d
docker compose -f researcher-runtime/compose.json logs --tail 30 bot
```

`create.py` only writes configuration. It never starts containers, allocates a
paid VM or copies credentials. For production distribution, push the image to
your own registry and pass its immutable digest as `--image`.

Approve the displayed device code and fingerprint in **Settings → Machines**.
Do not publish enrollment links or logs containing device codes. The private
volume retains enrollment across restarts. Unapproved device codes expire;
restart the deployment to request another code if needed.

After it joins, use the machine's Keys/Connections controls to configure the
model and integrations you want this bot to have. Keys are not automatically
copied from another bot or the desktop. Then open **Bots → New bot**, choose this
unused dedicated runtime and create the assistant. A runtime accepts exactly one
bot and refuses unrelated conversation engines. Its existing bot remains
available after a host/container restart.

Repeat the generation command with a different name and output directory for
every bot. Compose project names isolate the volumes and networks. Do not mount
another bot's volume, the host home directory or the Docker socket.

## Pause, stop and retention

**Pause bot** stops the assistant's background work; it does not stop billing for
the cloud host. `docker compose ... stop` stops the runtime. `down` removes the
container/network but retains the named volume. Removing the volume would remove
the bot's files, identity, keys, browser logins and history; back it up through
your infrastructure's secure storage mechanism first.

The dedicated runtime uses a persistent headless Chromium profile. Browser
cookies survive a normal browser/container restart because the profile is on the
bot's volume. Unexpected termination can still interrupt the last browser write.
Browser actions use existing tool permissions and URL guards. Sites that require
interactive human authentication or block automation can still require a separate
supported sign-in path; this deployment does not bypass their checks.

This image and deployment require live infrastructure qualification before a
production rollout. Configuration tests do not prove container isolation or
cloud connectivity in a particular provider environment.
