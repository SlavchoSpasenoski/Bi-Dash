---
title: "Bi-Dash: User Guide"
sidebar:
  order: 0
---

The complete [English user guide](https://github.com/SlavchoSpasenoski/Bi-Dash/blob/main/USER_GUIDE.md) is the starting point for this Bi-Dash version. It covers improvements over the imported Redash foundation, practical steps, prompt examples, AI settings, roles and troubleshooting.

## Start with your role

- **Reader:** Open a dashboard, check freshness and use available results. Downloads depend on policy; SQL access and fresh execution are unavailable.
- **Editor:** Create a query manually or open **AI Assistant → Build dashboard**. Select a permitted PostgreSQL/MySQL source, relevant tables and a specific prompt. Review the draft before publishing.
- **Admin:** Connect sources, configure groups and roles, and set up **Roles & Access**, **AI Configuration** and **AI Context**.

## Key rules

**Ask AI** is a conversation and does not execute SQL. **Build dashboard** creates an actual draft with queries and visualizations, then executes the queries sequentially. Publication is manual.

AI queries have no additional automatic 1,000-row limit. Use the **Limit 1000** checkbox in the query editor when needed; an explicit SQL LIMIT remains a separate condition. AI query duration is limited to 60 seconds or a stricter applicable limit.

Existing source groups and permissions combine with Admin/Editor/Reader roles. AI context does not grant database access. An unpublished draft does not guarantee author-only privacy.
