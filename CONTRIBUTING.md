# Contributing to Posterly

Thanks for your interest in improving Posterly! 🎉 This project is a self-hostable
photo-collage web app, and contributions of all sizes are welcome — bug reports,
docs, features, and polish.

## Ways to contribute

- 🐛 **Report a bug** — open an issue with steps to reproduce.
- 💡 **Suggest a feature** — open an issue describing the use case.
- 🛠️ **Send a PR** — fix a bug, add a feature, improve docs.

For anything non-trivial, please open an issue first so we can agree on the
approach before you invest time.

## Project layout

```
backend/app/      FastAPI app (auth, projects, rendering, OTP, email, config)
collage_a4.py     Pillow collage renderer (also a standalone CLI)
frontend/         React + Vite + TypeScript SPA
tests/            Python tests (unittest)
samples/          CC0 sample images + generator
docker-compose.yml / Dockerfile   Deployment
```

## Development setup

### Backend

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests        # run the test suite
```

### Frontend

```bash
cd frontend
npm install
npm run dev      # Vite dev server, proxies /api -> backend
npm run build    # type-check + production build
```

### Full app via Docker

```bash
cp .env.example .env   # set APP_ORIGIN + SESSION_SECRET
docker compose up -d --build
```

## Before you open a PR

- ✅ Backend tests pass: `python -m unittest discover -s tests`
- ✅ Frontend builds clean: `cd frontend && npm run build` (no type errors)
- ✅ No secrets, personal data, or real photos committed (see `.gitignore`)
- ✅ Keep changes focused; match the surrounding code style
- ✅ Update docs / `.env.example` when you add or change configuration

## Coding notes

- The backend runs as a **single worker** on purpose (in-memory rate limits,
  per-project file locks, per-email OTP counters). Avoid designs that assume
  multiple workers without externalizing that state.
- Storage is **filesystem-only** under `./data` with atomic writes — no database.
- Branding is **env-driven** (`BRAND_NAME` / `BRAND_TAGLINE`); never hardcode a
  brand name in code or assets.

## Commit & PR style

- Write clear, imperative commit messages ("Add X", "Fix Y").
- Reference related issues in the PR description.
- Small, reviewable PRs merge faster than large ones.

By contributing, you agree your contributions are licensed under the
[MIT License](LICENSE).
