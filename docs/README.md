# Bi-Dash Docs

This folder contains the Bi-Dash guide adapted from upstream Redash documentation. The upstream site at https://redash.io/help/ is independent; it does not publish this fork. Current Bi-Dash-specific behavior is documented in the root README, AI_DASHBOARDS.md and ACCESS_CONTROL.md.
The site that renders it lives in [`/website`](../website).

## Writing docs

- Every Markdown file is a page: `docs/user-guide/alerts/setting-up-an-alert.md`
  is served below your configured site origin at `/help/user-guide/alerts/setting-up-an-alert/`.
  A folder's `index.md` is the folder's own page (category pages list their
  articles automatically).
- **Don't rename or move published pages.** The Bi-Dash app and many external
  sites link to these URLs. If you must, add a redirect in
  `website/public/_redirects`.
- Frontmatter:
  ```yaml
  ---
  title: Setting Up An Alert      # required, shown as the page heading
  description: One-line summary   # optional, used for search engines and lists
  keywords: [alerts, threshold]   # optional
  sidebar:
    order: 1                      # optional, position within its category
  ---
  ```
- Callouts use [Starlight asides](https://starlight.astro.build/components/asides/):
  `:::note`, `:::tip`, `:::caution` and `:::danger`, closed with `:::`.
- Images: put new images next to the page and link them relatively
  (`![Alert settings](./alert-settings.png)`). Existing images are served from
  `website/public/assets/images/docs/` under their old URLs, because other sites
  link to them.
- Link to other pages with absolute paths like `/help/user-guide/querying/writing-queries/`.

## Versions

The inherited site supports versioned documentation builds. This fork does not publish to the upstream domain. Configure the site origin and release references for your deployment before using its multi-version publishing script.

## Previewing

```sh
cd website
pnpm install
pnpm dev        # http://localhost:4321/help/
```
