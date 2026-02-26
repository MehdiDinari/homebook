# Integration WordPress -> FastAPI

## 1) Prerequis WordPress
- Installer un plugin JWT compatible HS256.
- Utiliser la meme `JWT_SECRET` cote WP et cote FastAPI.
- Le token doit contenir: `sub` (ou `wp_user_id`), `email`, `display_name`, `roles`, `exp`.

## 2) Snippet PHP (exemple)

```php
<?php
add_action('wp_enqueue_scripts', function () {
    if (!is_user_logged_in()) {
        return;
    }

    $user = wp_get_current_user();
    $secret = 'change-me'; // utiliser une constante/secret manager
    $now = time();

    $payload = [
        'sub' => (string) $user->ID,
        'wp_user_id' => (int) $user->ID,
        'email' => $user->user_email,
        'display_name' => $user->display_name,
        'roles' => array_values((array) $user->roles),
        'iat' => $now,
        'exp' => $now + (15 * 60),
    ];

    // Si plugin JWT fournit encode utilitaire, reutiliser; sinon utiliser firebase/php-jwt.
    $token = \Firebase\JWT\JWT::encode($payload, $secret, 'HS256');

    wp_register_script('homebook-app', get_stylesheet_directory_uri() . '/js/homebook-app.js', [], null, true);
    wp_enqueue_script('homebook-app');

    wp_localize_script('homebook-app', 'HB_CONFIG', [
        'apiBase' => 'https://api.homebook.example.com/api/v1',
        'wsBase' => 'wss://api.homebook.example.com/ws',
        'token' => $token,
        'currentUser' => [
            'id' => (int) $user->ID,
            'email' => $user->user_email,
            'displayName' => $user->display_name,
        ],
    ]);
});
```

## 3) Exemple JS minimal

```js
async function hbGet(path) {
  const res = await fetch(`${HB_CONFIG.apiBase}${path}`, {
    headers: { Authorization: `Bearer ${HB_CONFIG.token}` },
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

const me = await hbGet('/auth/me');
console.log(me);

const ws = new WebSocket(`${HB_CONFIG.wsBase}/notifications?token=${encodeURIComponent(HB_CONFIG.token)}`);
ws.onmessage = (event) => console.log('notif', event.data);
```
