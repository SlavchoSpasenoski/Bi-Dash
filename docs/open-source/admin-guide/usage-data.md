---
title: Optional Usage Data
---

Bi-Dash inherits an optional aggregate usage-data feature. An administrator controls it through **Settings → General → Anonymous Usage Data Sharing**. Its recipient is the upstream service at `https://version.redash.io/subscribe`; it is not a Bi-Dash-operated endpoint. Rebranding the interface does not change that recipient.

The inherited payload contains aggregate installation counts and configured component types. Review `redash/tasks/general.py` in the deployed revision for the exact payload. This setting is separate from AI requests, which send prompts, schemas and configured context to the explicitly selected AI provider.
