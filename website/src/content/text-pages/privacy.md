---
title: Bi-Dash Privacy and Data Flow
layout: page
path: /privacy/
---

# Bi-Dash privacy and data flow

Bi-Dash is self-hosted software. The installation operator controls accounts, database connections, query results, logs, backups and retention. This page describes software behavior; it is not the privacy policy of a hosted service run by the project author. Ask your installation operator for its own privacy policy and contact details.

AI requests send your prompt, selected schema and configured context to the selected provider. Local Ollama keeps inference at the configured local endpoint; hosted providers apply their own processing terms. The dashboard builder does not send result rows back to the model. API keys are kept server-side and saved keys are encrypted using the installation secret.

The inherited optional usage-data feature, if enabled by an administrator, sends aggregated installation information to the upstream analytics endpoint at version.redash.io. Bi-Dash does not relabel that recipient as its own service. Deployment analytics, mail, authentication and integrations depend on the operator's configuration. Do not put secrets or personal information into AI context unless that use is authorized.
