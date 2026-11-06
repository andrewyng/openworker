# Troubleshooting

| Symptom | Check |
|---|---|
| Runtime not found | Install npm optional dependencies, or provide an absolute matching executable path. For packaged desktop apps, put it in resources outside ASAR. |
| Release mismatch | Update SDK and helper together. Do not substitute a different binary merely because it implements protocol 0.1. |
| No model or authentication failure | Confirm provider name, exact model ID, API format, endpoint and credential source. Never put keys into a prompt. |
| Run appears stopped | Inspect `session.state()` and interruptions. It may be waiting for approval, a question or a required safety check. |
| Safety check unavailable | Restore the configured handler/service, then explicitly resume safety. Bypass is not a way around it. |
| Lost connection during a tool call | Reattach and reconcile the durable outcome. Do not automatically retry a possibly completed write. |
| PostgreSQL schema mismatch | Drain old processes and follow the documented migration. Do not change the schema marker manually to downgrade. |
| App works in dev but not packaged | Check executable resources, architecture, signing, permissions and the package's actual working directory. |

For a useful bug report include the SDK/host versions, OS/CPU, execution/storage
profile, session/run IDs and sanitized error/event codes. Exclude API keys,
provider tokens, prompts, file contents and private state directories.

[Versions and compatibility](releases.md) · [Guardrails guide](guardrails.md)
