# API Contract (Summary)

Toutes les routes API utilisent `Authorization: Bearer <jwt>`.
Exceptions education dashboard (snippets WP): les routes education acceptent `X-WP-User-Id` et/ou `X-User-Email` (+ optionnel `X-WP-User-Roles`) pour l'auth des pages WordPress.

## Auth
- `GET /api/v1/auth/me`

## Profiles / Friends / Settings
- `GET|PATCH /api/v1/profiles/me`
- `POST /api/v1/friends/requests`
- `POST /api/v1/friends/requests/{id}/accept`
- `DELETE /api/v1/friends/{friend_wp_user_id}`
- `GET|PATCH /api/v1/settings/privacy`

## Catalog
- `GET /api/v1/catalog/books`
- `GET /api/v1/catalog/books/{work_id}`
- `POST /api/v1/catalog/favorites/{work_id}`
- `DELETE /api/v1/catalog/favorites/{work_id}`
- `PUT /api/v1/catalog/progress/{work_id}`
- `GET /api/v1/catalog/recommendations`

## Chats
- `POST /api/v1/chats/rooms`
- `GET /api/v1/chats/rooms`
- `GET /api/v1/chats/rooms/{room_id}/messages`
- `POST /api/v1/chats/rooms/{room_id}/messages`
- `WS /ws/chats/rooms/{room_id}?token=...`

## Education (Teachers / Students)
- `GET /api/v1/teachers`
- `POST /api/v1/teachers/{teacher_wp_user_id}/subscribe`
- `GET /api/v1/teachers/{teacher_wp_user_id}/students`
- `GET /api/v1/teachers/{teacher_wp_user_id}/sessions`
- `POST /api/v1/teachers/{teacher_wp_user_id}/sessions` (option: `student_wp_user_id` pour cours specifique)
- `GET /api/v1/teachers/{teacher_wp_user_id}/calendar`
- `GET /api/v1/teachers/{teacher_wp_user_id}/earnings` (argent reel en EUR)
- `GET /api/v1/teachers/{teacher_wp_user_id}/wallet` (solde disponible retrait)
- `GET /api/v1/teachers/{teacher_wp_user_id}/wallet/ledger`
- `POST /api/v1/teachers/{teacher_wp_user_id}/withdrawals`
- `GET /api/v1/teachers/{teacher_wp_user_id}/withdrawals`
- `PATCH /api/v1/teachers/{teacher_wp_user_id}/withdrawals/{withdrawal_id}`
- `GET /api/v1/students/{student_wp_user_id}/balance`
- `GET /api/v1/students/{student_wp_user_id}/money` (depot/depense EUR + points)
- `GET /api/v1/students/{student_wp_user_id}/wallet/topup/transactions?limit=100`
- `GET /api/v1/students/{student_wp_user_id}/wallet/ledger?limit=200`
- `GET /api/v1/students/{student_wp_user_id}/sessions`
- `GET /api/v1/students/{student_wp_user_id}/calendar`
- `GET /api/v1/users/{wp_user_id}/subscriptions`
- `PATCH /api/v1/sessions/{session_id}/schedule` (prof ou eleve cible)
- `GET /api/v1/sessions/{session_id}` (details session avec controle d'acces)
- `GET /api/v1/sessions/{session_id}/access` (URL d'acces live/cours)
- `POST /api/v1/sessions/{session_id}/join` (retro-compat, live/cours selon droits)
- `POST /api/v1/sessions/{session_id}/presence` (`event=joined|left`)
- `GET /api/v1/sessions/{session_id}/presence/online` (participants en ligne)
- `POST /api/v1/payments/checkouts` (`provider=auto|mock|stripe|paypal`)
- `POST /api/v1/payments/checkouts/{checkout_token}/confirm`
- `GET /api/v1/payments/transactions`
- `GET /api/v1/admin/revenue/summary`

Format evenement calendrier:
- `id` (ex: `teacher-session-123`)
- `session_id` (int, actionnable cote front)
- `kind` (`live` ou `course`)
- `status`, `starts_at`, `duration_minutes`, `teacher_name`, `student_wp_user_id`

Note PayPal:
- Le `checkout_token` de confirmation est l'`order_id` PayPal.
- Au retour PayPal, l'URL contient souvent `?token=<order_id>` (utiliser cette valeur pour `/confirm`).

Partage revenus cours:
- A chaque paiement de cours confirme, backend applique: 70% wallet prof / 30% plateforme(admin).
- Les retraits prof (`PATCH .../withdrawals/{id}` vers `paid`) peuvent declencher un payout PayPal si `method=paypal` et credentials serveur actifs.

## Social Feed
- `POST /api/v1/posts`
- `GET /api/v1/posts/feed`
- `POST /api/v1/posts/{id}/reactions`
- `POST /api/v1/posts/{id}/comments`
- `POST /api/v1/reports`

## Chatbot
- `GET /api/v1/chatbot/search?query=...&limit=...` (recherche livres pour widget chatbot)
- `GET /api/v1/chatbot/history?work_id=...` (cree/retourne la session courante du livre) -> `{session_id,work_id,messages,sources}`
- `POST /api/v1/chatbot/chat` body `{work_id,message}` -> `{answer,messages,sources}`
- `POST /api/v1/chatbot/reset` body `{work_id}` -> `{session_id,work_id,messages,sources}`
- `POST /api/v1/chatbot/sessions`
- `GET /api/v1/chatbot/sessions`
- `GET /api/v1/chatbot/sessions/{id}/messages`
- `POST /api/v1/chatbot/sessions/{id}/messages`
- `GET /api/v1/chatbot/sessions/{id}/export.txt`

## Search + Notifications + Help
- `GET /api/v1/search`
- `GET /api/v1/notifications`
- `POST /api/v1/notifications/{id}/read`
- `WS /ws/notifications?token=...`
- `WS /ws/sessions/{session_id}?token=...`
- `GET /api/v1/help/articles`
- `POST /api/v1/help/tickets`
- `GET /api/v1/help/tickets`
- `GET /api/v1/help/tickets/{ticket_id}`
- `PATCH /api/v1/help/tickets/{ticket_id}/status` (admin/support)
