# WhatIf? web

React + Vite + Tailwind front end for WhatIf?. It runs entirely on static demo data for now. No backend is needed.

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

- **Demo data** lives in `src/api/mock/`: `catalog.ts` holds five public-domain books with hand-built graphs, and `engine.ts` is a keyword-based stand-in for routing, retrieval and storyline edits. State such as accounts, uploads, storylines, op logs and checkpoints is kept in `localStorage`. Reset it from **My Shelf → Reset demo data**.
- **API contract**: `src/api/client.ts` defines the `Api` interface and `src/api/types.ts` the wire types (they mirror `app/repo/models.py`). `src/api/http.ts` is a ready-made client for the future FastAPI routes. Set `VITE_API_MODE=http` to use it once they exist.
- **Try it**: sign up, open *The Wonderful Wizard of Oz*, ask "Who is Dorothy?", then "What if Dorothy never took the Silver Shoes and befriended a dragon named Ember?" and watch the graph.
