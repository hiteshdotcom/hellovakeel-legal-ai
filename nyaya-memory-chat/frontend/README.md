# Nyaya.AI — Frontend (React + Vite + TypeScript)

A modern component-based frontend for the FastAPI backend in `../app`. Ports the
original single-file `web/index.html` into a maintainable React app while keeping
the "Trust & Authority" navy + gold legal identity (Newsreader serif for legal
prose, Outfit for UI chrome, JetBrains Mono for data).

## Features

- **Auth** — email/password + Google (Clerk / OAuth), cookie session via the backend.
- **Chat** — streaming NDJSON answers, recalled-memory pills, interactive citation
  footnotes, verification banners.
- **Sources** — right-hand drawer with a citation-map (SVG graph) and list view.
- **Judgment reader** — modal with Overview / Analysis / Full Text / Citations /
  Ask-AI tabs.
- Light/dark theme, responsive (mobile drawers), reduced-motion aware.

## Develop

The backend must be running on `:8000` (`uvicorn app.main:app --reload` from `..`).

```bash
npm install
npm run dev          # http://localhost:5173
```

Vite proxies `/api`, `/healthz`, and `/sso-callback` to the backend
(`vite.config.ts`), so the HttpOnly `nyaya_session` cookie is same-origin — no
CORS credentials needed. Override the target with `NYAYA_BACKEND=http://host:port`.

## Build for production

```bash
npm run build        # tsc + vite build -> ./dist
```

Serve `dist/` from FastAPI (point the `_WEB` mount at `frontend/dist`, or copy
`dist/*` over `web/`). The SPA calls same-origin `/api`, so no config changes are
needed once it is served by the backend.

## Structure

```
src/
  lib/        api client (+ NDJSON stream), types, icons, formatters, cn()
  store/      zustand: auth, chat, judgment, ui (theme/layout)
  components/
    auth/     AuthScreen
    layout/   AppShell, TopBar, Sidebar
    chat/     ChatView, Message, Answer (citations), Composer
    sources/  SourcesDrawer, CitationGraph
    judgment/ JudgmentReader, tabs, blocks, meta helpers
    ui.tsx    Button, Chip, Banner, Card, badges, skeletons
```
