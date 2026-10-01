# Page dependency tree

## `/` (GroundTruth agent workspace)

Entry: `frontend/src/main.tsx`

- `frontend/src/main.tsx`
  - `frontend/src/App.tsx` — controls, conversation, response, inspector.
    - `frontend/src/api.ts` — session-aware JSON API client.
    - `frontend/src/types.ts` — Python API response contracts.
    - `frontend/src/app.css` — dark theme and responsive layout.
  - `frontend/vite.config.ts` — React plugin and Python API proxy.
  - Python service: `src/web/server.py` and `src/web/service.py`.
