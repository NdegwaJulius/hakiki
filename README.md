# Hakiki

Kenya regulatory data APIs. Hakiki turns the PDFs that Kenyan regulators publish (SASRA SACCO schedules, Central Bank of Kenya entity lists, and more) into a searchable, rate-limited JSON API with a web dashboard.

**Who it's for:** developers, fintechs and researchers who need machine-readable data on regulated Kenyan financial institutions instead of scraping PDFs by hand.

## Features

- **REST API** (FastAPI) with versioned routes, e.g. `/cbk/v1/...`, filter and stats endpoints, and OpenAPI docs (admin only).
- **CBK module:** five entity types, with `/filters` and `/stats` endpoints.
- **SASRA SACCO data:** loaded from regulator PDFs via the scrapers.
- **API keys and tiers:** Free, Pro and Enterprise tiers with monthly call quotas and search/list limits, enforced by rate limiting (`slowapi`).
- **Web app:** registration, login, password reset, dashboard, key rotation, billing page.
- **Admin console:** PDF upload with a live processing log, data browser with Chart.js charts, system status page, tier configuration, finance view and user management.
- **PDF ingestion:** extracts tables and the regulator's document date using `pdfplumber`, `camelot` and `PyPDF2`.

## Built with

Python 3.12, FastAPI, SQLAlchemy (async), SQLite for local development, PostgreSQL/Supabase for production, Jinja2 templates, Chart.js. Deployable with Docker or Railway.

## Quick start

```bash
git clone https://github.com/NdegwaJulius/hakiki.git
cd hakiki
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit values; generate SECRET_KEY as described inside
python seed.py              # optional: load SACCO data
python seed_cbk.py          # optional: load CBK data
uvicorn app.main:app --reload
```

Open http://localhost:8000. To get an API key, register an account on the web app, or set `DEV_API_KEY` in `.env` to seed a local dev user.

### Docker

```bash
cp .env.example .env   # also set POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
docker compose up --build
```

### Configuration

All settings come from environment variables; see [`.env.example`](.env.example). Never commit your real `.env`.

### Smoke tests

With the server running:

```bash
python test_cbk.py --base-url http://localhost:8000 --api-key <your-key>
```

## Project layout

```
app/                FastAPI app (routers, models, tier config, rate limiter)
frontend/templates/ Jinja2 pages
migrations/         SQL schema for self-hosted Postgres
supabase/           Supabase config and migrations
scrape_*.py         PDF scrapers (SASRA, CBK)
seed*.py            Data loaders
```

## Roadmap

- [ ] Proper automated test suite (current tests are live-server smoke tests)
- [ ] More regulators (e.g. CMA, IRA, RBA)
- [ ] Scheduled scraping with change detection
- [ ] Public API documentation page
- [ ] Payment provider integration for paid tiers

## License

[MIT](LICENSE)
