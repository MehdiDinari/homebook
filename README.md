# HomeBook Unified Backend (FastAPI)

Backend unifie pour HomeBook: WordPress en front (pages HTML/CSS/JS + snippets PHP) et FastAPI en backend.

## Stack
- FastAPI + WebSocket
- PostgreSQL (SQLAlchemy async + Alembic)
- Redis (Pub/Sub + ARQ)
- Ollama local (chatbot gratuit)
- S3-compatible (MinIO par defaut)

## Endpoints principaux
- `GET /api/v1/auth/me`
- `GET|PATCH /api/v1/profiles/me`
- `POST /api/v1/friends/requests`
- `POST /api/v1/friends/requests/{id}/accept`
- `DELETE /api/v1/friends/{user_id}`
- `GET /api/v1/catalog/books`
- `GET /api/v1/catalog/books/{work_id}`
- `POST|DELETE /api/v1/catalog/favorites/{work_id}`
- `PUT /api/v1/catalog/progress/{work_id}`
- `POST /api/v1/chats/rooms`
- `GET /api/v1/chats/rooms`
- `GET /api/v1/chats/rooms/{room_id}/messages`
- `POST /api/v1/chats/rooms/{room_id}/messages`
- `POST /api/v1/posts`
- `GET /api/v1/posts/feed`
- `POST /api/v1/posts/{id}/reactions`
- `POST /api/v1/posts/{id}/comments`
- `POST /api/v1/reports`
- `POST /api/v1/chatbot/sessions`
- `GET /api/v1/chatbot/sessions`
- `POST /api/v1/chatbot/sessions/{id}/messages`
- `GET /api/v1/chatbot/sessions/{id}/export.txt`
- `GET /api/v1/search?q=...&types=books,users,rooms,posts`
- `GET /api/v1/notifications`
- `POST /api/v1/notifications/{id}/read`
- `GET|PATCH /api/v1/settings/privacy`
- `GET /api/v1/help/articles`

WebSocket:
- `GET /ws/chats/rooms/{room_id}?token=<jwt>`
- `GET /ws/notifications?token=<jwt>`

## Setup local

```bash
cp .env.example .env
docker compose up -d --build
```

Migration:

```bash
docker compose exec api alembic upgrade head
```

Ollama model pull (optionnel au premier run):

```bash
docker compose exec ollama ollama pull llama3.1:8b-instruct
```

Paiement (optionnel):
- Stripe: `STRIPE_SECRET_KEY`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`
- PayPal: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_ENV=sandbox|live`
- Repartition cours: prof 70% / admin 30% (fixe cote backend)

Docs API:
- `http://localhost:8000/docs`

## Lancement sans Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Worker ARQ

```bash
arq app.workers.arq_worker.WorkerSettings
```

## Notes securite
- Ne jamais versionner `.env`.
- Rotation immediate des anciens secrets exposes dans les anciens ZIP.
- JWT WordPress signe HS256 cote WP, verifie cote FastAPI.
