# Bi-Dash bilingual marketing website

The website has Macedonian (`/`) and English (`/en/`) routes. Each page has an accessible MK/EN language switch. Normal links work without JavaScript; optional local storage remembers the language when visitors return to `/`. Language switches retain the current anchor.

`index.html` is the Macedonian homepage source. `translations.en.json` provides English copy, including metadata and accessibility labels. `mk.html` and `en.html` are generated entrypoints used by Astro. Runtime assets live in `website/public/landing.css`, `landing.js`, and `language.js`.

Build and validate from the repository root:

```
node website/scripts/build-landing.mjs
node website/scripts/check-landing-locales.mjs
```

The build writes a self-contained `dist/`, creates both languages' download pages and guide hubs, and syncs generated static routes into `website/public/` for Astro. The existing Astro `/help/` documentation remains intact; marketing navigation uses `/guides/`.

Six Macedonian practical guides live in `guides/mk/`. Installation command blocks are filled from the repository installation guide. English routes retain the full original technical references. Other original project and legal documents keep their language and attribution, with English reference content explicitly labeled in the Macedonian shell.

Downloads use the public repository's main-branch source ZIP, not native installers. Platform verification caveats are preserved.

Sites hosting identity is in `.openai/hosting.json`. Publication was blocked by automatic review in the original session; do not publish until the user explicitly authorizes hosting. Never package the parent application's credentials, local configuration or data.
