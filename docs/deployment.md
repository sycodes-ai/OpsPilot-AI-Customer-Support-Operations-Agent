# Deployment

The repository builds into **one Docker image**: the React app is compiled in a Node stage and served by FastAPI, so the whole demo runs as a single web service with one public URL.

## Option A: Render (free web service)

1. Push this repository to GitHub.
2. On [render.com](https://render.com) choose **New → Web Service**, connect the repository and select **Docker** as the runtime. Render reads the `Dockerfile` and sets `PORT` automatically.
3. Add environment variables:
   | Variable | Value |
   |---|---|
   | `LLM_API_KEY` | your API key (leave empty to run in rules mode) |
   | `LLM_MODEL` | e.g. `gpt-4o-mini` |
   | `LLM_BASE_URL` | `https://api.openai.com/v1` or another OpenAI-compatible endpoint |
   | `RATE_LIMIT_PER_MINUTE` | `20` (protects your API budget) |
4. Deploy. Health check path: `/api/health`.

Without `DATABASE_URL` the app uses SQLite inside the container and re-creates the synthetic data on every start, which is fine for a demo. For a persistent database, create a free PostgreSQL database (for example on Neon or Render) and set `DATABASE_URL=postgresql://...`.

Free instances sleep when idle, so the first request after a pause can take 30–60 seconds. Mention this next to the demo link.

## Option B: any Docker host

```bash
docker build -t opspilot .
docker run -p 8000:8000 -e LLM_API_KEY=sk-... opspilot
```

## Option C: local with PostgreSQL

```bash
cp .env.example .env        # add LLM_API_KEY if you have one
docker compose up --build   # app on http://localhost:8000, PostgreSQL in a second container
```

## Keeping the public demo safe

- Only synthetic data is used; no real customer information should ever be loaded.
- Order details need the order number **and** the matching email.
- `/api/chat` is rate-limited per IP; tune `RATE_LIMIT_PER_MINUTE`.
- Set a monthly spending limit on your LLM provider account.
- Restrict `CORS_ORIGINS` if the frontend is hosted on a different domain.
