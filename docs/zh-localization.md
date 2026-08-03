# Chinese community localization

This document records a community-maintained Chinese localization of OpenWorker.
It is intentionally linked from upstream rather than merged as a full downstream
distribution so that the main project can keep its release artifacts, hosted
site, and localization strategy cleanly separated.

## Resources

- Localized repository: <https://github.com/zhanglunet/openworker-zh-localized>
- Chinese guide site: <https://oaosf.cn>
- macOS Apple Silicon download: <https://github.com/zhanglunet/openworker-zh-localized/releases/tag/v0.1.6-zh>
- Source analysis page: <https://oaosf.cn/source-analysis>
- Architecture infographic: <https://oaosf.cn/infographic>
- Changelog and weekly reports: <https://oaosf.cn/updates>

## Scope

The localized repository currently focuses on:

- Chinese desktop and GUI copy for the OpenWorker app.
- Chinese setup, usage, and troubleshooting documentation.
- A Chinese website explaining OpenWorker's capabilities, privacy model,
  architecture, connector surface, MCP support, and runtime flow.
- A downloadable macOS Apple Silicon community build for users who want to try
  the Chinese interface.
- Generated source-analysis, changelog, and weekly-report documents that stay in
  the localized repository and refresh as that repository evolves.

The localized build remains a community distribution. It should not be treated as
an official signed or notarized OpenWorker release unless the upstream maintainers
choose to adopt and operate that release path.

## Notes for maintainers

The downstream repository includes site deployment configuration, generated
documentation, screenshots, and downloadable binary artifacts. Those are useful
for Chinese-speaking users, but they are intentionally not included in this PR to
avoid mixing upstream source changes with downstream distribution assets.

If OpenWorker later adds first-class internationalization support, the Chinese
copy and workflow notes in the localized repository can serve as a reference
corpus for a smaller i18n-oriented contribution.
