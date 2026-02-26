/**
 * =========================================================
 * HomeBook — Snippet unique corrigé
 * (HB_CONFIG + JWT + proxy API WP->FastAPI + redirects + topbar/menu)
 * Type: PHP snippet (Run everywhere), sans <?php
 * =========================================================
 */

/* ---------- CONFIG ---------- */
if (!defined('HBX_JWT_SECRET')) {
    define('HBX_JWT_SECRET', 'lafrime');
}
if (!defined('HBX_TOKEN_TTL')) {
    define('HBX_TOKEN_TTL', 15 * 60);
}
if (!defined('HBX_USE_WP_PROXY')) {
    define('HBX_USE_WP_PROXY', true);
}
if (!defined('HBX_BACKEND_ORIGIN')) {
    // Tunnel public vers ton backend local (temporaire)
    define('HBX_BACKEND_ORIGIN', 'https://letting-adopt-looks-embassy.trycloudflare.com');
}
if (!defined('HBX_PUBLIC_API_BASE')) {
    // Utilisé seulement si HBX_USE_WP_PROXY = false
    define('HBX_PUBLIC_API_BASE', '');
}
if (!defined('HBX_API_SSLVERIFY')) {
    define('HBX_API_SSLVERIFY', true);
}

/* ---------- HELPERS ---------- */
if (!function_exists('hbx_debug_mode')) {
    function hbx_debug_mode() {
        return isset($_GET['hb_debug_api']) && $_GET['hb_debug_api'] === '1';
    }
}

if (!function_exists('hbx_unique_id')) {
    function hbx_unique_id($prefix = 'hbx') {
        return $prefix . '_' . wp_generate_password(6, false, false);
    }
}

if (!function_exists('hbx_b64url_encode')) {
    function hbx_b64url_encode($data) {
        return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
    }
}

if (!function_exists('hbx_b64url_decode')) {
    function hbx_b64url_decode($data) {
        $pad = strlen($data) % 4;
        if ($pad > 0) {
            $data .= str_repeat('=', 4 - $pad);
        }
        $raw = base64_decode(strtr($data, '-_', '+/'), true);
        return $raw === false ? '' : $raw;
    }
}

if (!function_exists('hbx_jwt_hs256')) {
    function hbx_jwt_hs256($payload, $secret) {
        $header = ['alg' => 'HS256', 'typ' => 'JWT'];
        $h = hbx_b64url_encode(wp_json_encode($header));
        $p = hbx_b64url_encode(wp_json_encode($payload));
        $sig = hash_hmac('sha256', $h . '.' . $p, $secret, true);
        return $h . '.' . $p . '.' . hbx_b64url_encode($sig);
    }
}

if (!function_exists('hbx_decode_jwt_claims_verified')) {
    function hbx_decode_jwt_claims_verified($jwt, $secret) {
        $parts = explode('.', (string) $jwt);
        if (count($parts) !== 3) {
            return null;
        }

        $h64 = $parts[0];
        $p64 = $parts[1];
        $s64 = $parts[2];

        $sig = hbx_b64url_decode($s64);
        if ($sig === '') {
            return null;
        }

        $expected = hash_hmac('sha256', $h64 . '.' . $p64, $secret, true);
        if (!hash_equals($expected, $sig)) {
            return null;
        }

        $payloadRaw = hbx_b64url_decode($p64);
        if ($payloadRaw === '') {
            return null;
        }

        $payload = json_decode($payloadRaw, true);
        if (!is_array($payload)) {
            return null;
        }

        if (isset($payload['exp']) && time() >= (int) $payload['exp']) {
            return null;
        }

        return $payload;
    }
}

if (!function_exists('hbx_normalize_api_base')) {
    function hbx_normalize_api_base($raw) {
        $b = rtrim(trim((string) $raw), '/');
        if ($b === '') {
            return '';
        }
        if (preg_match('#/api/v1$#i', $b)) {
            return $b;
        }
        if (preg_match('#/api$#i', $b)) {
            return $b . '/v1';
        }
        if (preg_match('#^https?://#i', $b) || strpos($b, '/') === 0) {
            return $b . '/api/v1';
        }
        return '';
    }
}

if (!function_exists('hbx_backend_api_base')) {
    function hbx_backend_api_base() {
        return hbx_normalize_api_base(HBX_BACKEND_ORIGIN);
    }
}

if (!function_exists('hbx_public_api_base')) {
    function hbx_public_api_base() {
        return hbx_normalize_api_base(HBX_PUBLIC_API_BASE);
    }
}

if (!function_exists('hbx_front_api_base')) {
    function hbx_front_api_base() {
        if (HBX_USE_WP_PROXY) {
            return rtrim(home_url('/wp-json/homebook/v1/proxy/api/v1'), '/');
        }
        $b = hbx_public_api_base();
        return $b !== '' ? $b : '/api/v1';
    }
}

if (!function_exists('hbx_role')) {
    function hbx_role() {
        if (!is_user_logged_in()) {
            return '';
        }

        $u = wp_get_current_user();
        $roles = (array) $u->roles;

        if (in_array('administrator', $roles, true)) {
            return 'administrator';
        }
        if (in_array('prof', $roles, true) || in_array('teacher', $roles, true) || in_array('instructor', $roles, true)) {
            return 'prof';
        }
        if (in_array('student', $roles, true)) {
            return 'student';
        }

        return '';
    }
}

if (!function_exists('hbx_menu_for_role')) {
    function hbx_menu_for_role($role) {
        if ($role === 'prof') {
            return 'prof';
        }
        if ($role === 'student') {
            return 'student';
        }
        return '';
    }
}

if (!function_exists('hbx_build_config')) {
    function hbx_build_config() {
        $cfg = [
            'apiBase'     => hbx_front_api_base(),
            'token'       => '',
            'currentUser' => null,
        ];

        if (!is_user_logged_in()) {
            return $cfg;
        }

        $u = wp_get_current_user();
        $now = time();

        $payload = [
            'sub'          => (string) $u->ID,
            'wp_user_id'   => (int) $u->ID,
            'email'        => (string) $u->user_email,
            'display_name' => (string) $u->display_name,
            'roles'        => array_values((array) $u->roles),
            'iat'          => $now,
            'exp'          => $now + (int) HBX_TOKEN_TTL,
        ];

        $cfg['token'] = hbx_jwt_hs256($payload, HBX_JWT_SECRET);
        $cfg['currentUser'] = [
            'id'          => (int) $u->ID,
            'wp_user_id'  => (int) $u->ID,
            'user_id'     => (int) $u->ID,
            'email'       => (string) $u->user_email,
            'displayName' => (string) $u->display_name,
            'name'        => (string) $u->display_name,
            'roles'       => array_values((array) $u->roles),
        ];

        return $cfg;
    }
}

/* ---------- INJECT HB_CONFIG + HB_USER + FETCH PATCH ---------- */
add_action('wp_head', function () {
    if (is_admin()) {
        return;
    }

    $cfg = hbx_build_config();

    echo '<script id="homebook-config">window.HB_CONFIG='
        . wp_json_encode($cfg, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)
        . ';window.HB_USER=(window.HB_CONFIG&&window.HB_CONFIG.currentUser)?{'
        . '"wp_id":window.HB_CONFIG.currentUser.wp_user_id||window.HB_CONFIG.currentUser.id||null,'
        . '"id":window.HB_CONFIG.currentUser.id||window.HB_CONFIG.currentUser.wp_user_id||null,'
        . '"user_id":window.HB_CONFIG.currentUser.user_id||window.HB_CONFIG.currentUser.id||null,'
        . '"email":window.HB_CONFIG.currentUser.email||"",'
        . '"name":window.HB_CONFIG.currentUser.displayName||window.HB_CONFIG.currentUser.name||"",'
        . '"displayName":window.HB_CONFIG.currentUser.displayName||window.HB_CONFIG.currentUser.name||"",'
        . '"roles":window.HB_CONFIG.currentUser.roles||[]'
        . '}:null;</script>' . "\n";

    echo '<script id="hbx-fetch-patch">(function(){'
        . 'if(window.__HBX_FETCH_PATCH__)return;window.__HBX_FETCH_PATCH__=true;'
        . 'var t=(window.HB_CONFIG&&window.HB_CONFIG.token)?String(window.HB_CONFIG.token):"";'
        . 'if(!t||!window.fetch)return;'
        . 'var of=window.fetch;'
        . 'window.fetch=function(input,init){'
        . 'try{'
        . 'var url=(typeof input==="string")?input:((input&&input.url)?input.url:"");'
        . 'if(url&&url.indexOf("/wp-json/homebook/v1/proxy/")!==-1){'
        . 'init=init||{};'
        . 'var h=new Headers(init.headers||{});'
        . 'if(!h.has("X-HB-Token"))h.set("X-HB-Token",t);'
        . 'if(!h.has("Authorization"))h.set("Authorization","Bearer "+t);'
        . 'init.headers=h;'
        . 'if(!init.credentials)init.credentials="same-origin";'
        . '}'
        . '}catch(e){}'
        . 'return of(input,init);'
        . '};'
        . '})();</script>' . "\n";
}, 5);

add_shortcode('hb_inject_user', function () {
    $cfg = hbx_build_config();
    return '<script>window.HB_USER=' . wp_json_encode($cfg['currentUser'] ?? null, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . ';</script>';
});

/* ---------- PROXY WP REST -> FASTAPI ---------- */
if (!function_exists('hbx_token_from_request')) {
    function hbx_token_from_request($request) {
        $xhb = trim((string) $request->get_header('x-hb-token'));
        if ($xhb !== '') {
            return $xhb;
        }

        $auth = trim((string) $request->get_header('authorization'));
        if ($auth !== '' && stripos($auth, 'bearer ') === 0) {
            return trim(substr($auth, 7));
        }

        return '';
    }
}

if (!function_exists('hbx_proxy_permission')) {
    function hbx_proxy_permission($request) {
        $path = ltrim((string) $request->get_param('path'), '/');
        if ($path === '') {
            return new WP_Error('hbx_proxy_bad_path', 'Missing path', ['status' => 400]);
        }

        if (strpos($path, 'api/v1/help/articles') === 0) {
            return true;
        }

        if (is_user_logged_in()) {
            return true;
        }

        $token = hbx_token_from_request($request);
        if ($token !== '') {
            $claims = hbx_decode_jwt_claims_verified($token, HBX_JWT_SECRET);
            if (is_array($claims)) {
                return true;
            }
        }

        return new WP_Error('hbx_proxy_unauthorized', 'Unauthorized', ['status' => 401]);
    }
}

if (!function_exists('hbx_proxy_to_backend')) {
    function hbx_proxy_to_backend($request) {
        $path = ltrim((string) $request->get_param('path'), '/');
        if ($path === '') {
            return new WP_REST_Response(['detail' => 'Missing path'], 400);
        }

        if ($path !== 'api/v1' && strpos($path, 'api/v1/') !== 0) {
            $path = 'api/v1/' . $path;
        }

        $base = hbx_backend_api_base();
        if ($base === '') {
            return new WP_REST_Response([
                'detail' => 'HBX_BACKEND_ORIGIN not configured',
                'hint'   => 'Set HBX_BACKEND_ORIGIN to your real FastAPI origin',
            ], 500);
        }

        $sub = preg_replace('#^api/v1/?#i', '', $path);
        $target = rtrim($base, '/');
        if ($sub !== '') {
            $target .= '/' . ltrim((string) $sub, '/');
        }

        $query = $request->get_query_params();
        if (!empty($query)) {
            $target .= (strpos($target, '?') === false ? '?' : '&') . http_build_query($query);
        }

        $headers = ['Accept' => 'application/json'];
        $contentType = (string) $request->get_header('content-type');
        if ($contentType !== '') {
            $headers['Content-Type'] = $contentType;
        }

        $token = hbx_token_from_request($request);
        if ($token !== '') {
            $headers['Authorization'] = 'Bearer ' . $token;
        }

        $wp_user_id = null;
        $email = '';
        $roles = [];

        if (is_user_logged_in()) {
            $u = wp_get_current_user();
            $wp_user_id = (int) $u->ID;
            $email = (string) $u->user_email;
            $roles = array_values((array) $u->roles);
        } elseif ($token !== '') {
            $claims = hbx_decode_jwt_claims_verified($token, HBX_JWT_SECRET);
            if (is_array($claims)) {
                if (isset($claims['wp_user_id'])) {
                    $wp_user_id = (int) $claims['wp_user_id'];
                } elseif (isset($claims['sub'])) {
                    $wp_user_id = (int) $claims['sub'];
                }
                $email = isset($claims['email']) ? (string) $claims['email'] : '';
                $roles = (isset($claims['roles']) && is_array($claims['roles'])) ? $claims['roles'] : [];
            }
        }

        if (!empty($wp_user_id)) {
            $headers['X-WP-User-Id'] = (string) (int) $wp_user_id;
        }
        if ($email !== '') {
            $headers['X-User-Email'] = $email;
        }
        if (!empty($roles)) {
            $headers['X-WP-User-Roles'] = implode(',', array_map('strval', $roles));
        }

        $method = strtoupper((string) $request->get_method());
        $args = [
            'method'    => $method,
            'headers'   => $headers,
            'timeout'   => 30,
            'sslverify' => (bool) HBX_API_SSLVERIFY,
        ];

        $body = (string) $request->get_body();
        if ($body !== '' && !in_array($method, ['GET', 'HEAD', 'DELETE'], true)) {
            $args['body'] = $body;
        }

        $res = wp_remote_request($target, $args);
        if (is_wp_error($res)) {
            return new WP_REST_Response([
                'detail' => $res->get_error_message(),
                'target' => $target,
            ], 502);
        }

        $status = (int) wp_remote_retrieve_response_code($res);
        $raw = (string) wp_remote_retrieve_body($res);
        $content_type = (string) wp_remote_retrieve_header($res, 'content-type');
        $cache_control = (string) wp_remote_retrieve_header($res, 'cache-control');

        $is_json = false;
        if ($content_type !== '') {
            $ct = strtolower($content_type);
            $is_json = (strpos($ct, 'application/json') !== false) || (strpos($ct, '+json') !== false);
        } else {
            $trim = ltrim($raw);
            $is_json = ($trim !== '' && ($trim[0] === '{' || $trim[0] === '['));
        }

        if ($is_json) {
            $decoded = json_decode($raw, true);
            $payload = is_array($decoded) ? $decoded : ['raw' => $raw];
            $response = new WP_REST_Response($payload, $status ?: 502);
            if ($content_type !== '') {
                $response->header('Content-Type', $content_type);
            }
            if ($cache_control !== '') {
                $response->header('Cache-Control', $cache_control);
            }
            return $response;
        }

        $response = new WP_REST_Response($raw, $status ?: 502);
        if ($content_type !== '') {
            $response->header('Content-Type', $content_type);
        }
        if ($cache_control !== '') {
            $response->header('Cache-Control', $cache_control);
        }
        return $response;
    }
}

add_action('rest_api_init', function () {
    register_rest_route('homebook/v1', '/proxy/(?P<path>.*)', [
        [
            'methods'             => WP_REST_Server::ALLMETHODS,
            'callback'            => 'hbx_proxy_to_backend',
            'permission_callback' => 'hbx_proxy_permission',
        ],
    ]);
});

/* ---------- REDIRECTS ---------- */
if (!function_exists('hbx_login_url')) {
    function hbx_login_url() {
        return home_url('/connexion/');
    }
}
if (!function_exists('hbx_prof_url')) {
    function hbx_prof_url() {
        return home_url('/prof/');
    }
}
if (!function_exists('hbx_etudiant_url')) {
    function hbx_etudiant_url() {
        return home_url('/etudiant/');
    }
}

if (!function_exists('hbx_user_target_url')) {
    function hbx_user_target_url($user) {
        $roles = (array) $user->roles;

        if (in_array('administrator', $roles, true)) {
            return admin_url();
        }
        if (in_array('prof', $roles, true) || in_array('teacher', $roles, true) || in_array('instructor', $roles, true)) {
            return hbx_prof_url();
        }
        if (in_array('student', $roles, true)) {
            return hbx_etudiant_url();
        }

        return home_url('/');
    }
}

add_filter('login_redirect', function ($redirect_to, $requested_redirect_to, $user) {
    if (!($user instanceof WP_User)) {
        return $redirect_to;
    }
    return hbx_user_target_url($user);
}, 99, 3);

add_filter('logout_redirect', function ($redirect_to, $requested_redirect_to, $user) {
    return home_url('/');
}, 9999, 3);

// Force every generated logout URL to carry redirect_to=home.
add_filter('logout_url', function ($logout_url, $redirect) {
    $clean = remove_query_arg('redirect_to', (string) $logout_url);
    return add_query_arg('redirect_to', rawurlencode(home_url('/')), $clean);
}, 9999, 2);

// Some plugins still land on wp-login.php?loggedout=true after logout.
add_action('login_init', function () {
    $is_logged_out_landing = isset($_GET['loggedout']) && $_GET['loggedout'] === 'true';
    if ($is_logged_out_landing) {
        wp_safe_redirect(home_url('/'));
        exit;
    }
}, 1);

add_action('template_redirect', function () {
    if (!is_user_logged_in()) {
        return;
    }

    $path = trim((string) parse_url($_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH), '/');
    if ($path !== 'connexion') {
        return;
    }

    wp_safe_redirect(hbx_user_target_url(wp_get_current_user()));
    exit;
});

add_action('init', function () {
    if ((defined('DOING_AJAX') && DOING_AJAX) || (function_exists('wp_doing_ajax') && wp_doing_ajax())) {
        return;
    }

    if (is_admin() && is_user_logged_in()) {
        $u = wp_get_current_user();
        if (!in_array('administrator', (array) $u->roles, true)) {
            wp_safe_redirect(hbx_user_target_url($u));
            exit;
        }
    }
});

add_action('template_redirect', function () {
    if (is_admin() || (function_exists('wp_doing_ajax') && wp_doing_ajax())) {
        return;
    }

    $path = trim((string) parse_url($_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH), '/');
    $is_prof = ($path === 'prof');
    $is_etudiant = ($path === 'etudiant');

    if (!$is_prof && !$is_etudiant) {
        return;
    }

    if (!is_user_logged_in()) {
        wp_safe_redirect(hbx_login_url());
        exit;
    }

    $u = wp_get_current_user();
    $roles = (array) $u->roles;

    $is_admin = in_array('administrator', $roles, true);
    $is_prof_role = in_array('prof', $roles, true) || in_array('teacher', $roles, true) || in_array('instructor', $roles, true);
    $is_student_role = in_array('student', $roles, true);

    if ($is_prof && !$is_admin && !$is_prof_role) {
        wp_safe_redirect($is_student_role ? hbx_etudiant_url() : home_url('/'));
        exit;
    }

    if ($is_etudiant && !$is_admin && !$is_student_role) {
        wp_safe_redirect($is_prof_role ? hbx_prof_url() : home_url('/'));
        exit;
    }
});

/* ---------- KPI HELPERS ---------- */
if (!function_exists('hbx_api_headers_for_user')) {
    function hbx_api_headers_for_user($u) {
        return [
            'Accept'          => 'application/json',
            'X-WP-User-Id'    => (string) (int) $u->ID,
            'X-User-Email'    => (string) $u->user_email,
            'X-WP-User-Roles' => implode(',', array_map('strval', (array) $u->roles)),
        ];
    }
}

if (!function_exists('hbx_api_get_json')) {
    function hbx_api_get_json($path, $u, &$http_code = null, &$error = null) {
        $http_code = null;
        $error = null;

        $base = hbx_backend_api_base();
        if ($base === '') {
            $http_code = 0;
            $error = 'HBX_BACKEND_ORIGIN not configured';
            return null;
        }

        $url = rtrim($base, '/') . '/' . ltrim((string) $path, '/');
        $res = wp_remote_get($url, [
            'timeout'   => 8,
            'headers'   => hbx_api_headers_for_user($u),
            'sslverify' => (bool) HBX_API_SSLVERIFY,
        ]);

        if (is_wp_error($res)) {
            $http_code = 0;
            $error = $res->get_error_message();
            return null;
        }

        $http_code = (int) wp_remote_retrieve_response_code($res);
        $body = (string) wp_remote_retrieve_body($res);

        $json = null;
        if ($body !== '') {
            $decoded = json_decode($body, true);
            if (is_array($decoded)) {
                $json = $decoded;
            }
        }

        if ($http_code < 200 || $http_code >= 300) {
            $error = (is_array($json) && !empty($json['detail'])) ? (string) $json['detail'] : ('HTTP ' . $http_code);
            return null;
        }

        return is_array($json) ? $json : [];
    }
}

if (!function_exists('hbx_format_eur_from_cents')) {
    function hbx_format_eur_from_cents($cents) {
        $v = ((int) $cents) / 100;
        if (function_exists('number_format_i18n')) {
            return number_format_i18n($v, 2) . ' €';
        }
        return number_format($v, 2, ',', ' ') . ' €';
    }
}

if (!function_exists('hbx_prof_salary_data')) {
    function hbx_prof_salary_data($u) {
        $cache_key = 'hbx_prof_salary_' . (int) $u->ID;
        $cached = get_transient($cache_key);
        if (is_array($cached) && !empty($cached['label'])) {
            return $cached;
        }

        $code = null;
        $err = null;
        $data = hbx_api_get_json('/teachers/' . (int) $u->ID . '/earnings', $u, $code, $err);

        if (is_array($data) && isset($data['earnings_cents'])) {
            $out = [
                'label' => hbx_format_eur_from_cents((int) $data['earnings_cents']),
                'ok'    => true,
                'hint'  => 'Revenus nets prof',
            ];
            set_transient($cache_key, $out, 60);
            return $out;
        }

        return [
            'label' => hbx_debug_mode() ? ('ERR ' . (string) ($code ?? 'API')) : '—',
            'ok'    => false,
            'hint'  => $err ?: 'Echec API salaire',
        ];
    }
}

if (!function_exists('hbx_student_points_data')) {
    function hbx_student_points_data($u) {
        $cache_key = 'hbx_student_points_' . (int) $u->ID;
        $cached = get_transient($cache_key);
        if (is_array($cached) && !empty($cached['label'])) {
            return $cached;
        }

        $code = null;
        $err = null;
        $data = hbx_api_get_json('/students/' . (int) $u->ID . '/money', $u, $code, $err);

        if (is_array($data) && isset($data['points_balance'])) {
            $out = [
                'label' => ((int) $data['points_balance']) . ' pts',
                'ok'    => true,
                'hint'  => 'Solde points',
            ];
            set_transient($cache_key, $out, 60);
            return $out;
        }

        return [
            'label' => hbx_debug_mode() ? ('ERR ' . (string) ($code ?? 'API')) : '—',
            'ok'    => false,
            'hint'  => $err ?: 'Echec API points',
        ];
    }
}

if (!function_exists('hbx_resolve_user_avatar_url')) {
    function hbx_resolve_user_avatar_url($user_id, $size = 80) {
        $size = max(24, (int) $size);
        $default = 'https://www.gravatar.com/avatar/?d=mp&s=' . $size;

        $avatar = (string) get_avatar_url((int) $user_id, ['size' => $size, 'default' => 'mp']);
        $is_placeholder = ($avatar === '') || (bool) preg_match('#gravatar\.com/avatar/\?d=#i', $avatar);
        if (!$is_placeholder) {
            return $avatar;
        }

        $meta_keys = [
            'profile_magic_profile_image',
            'pm_profile_image',
            'profile_image',
            'user_avatar',
            'wp_user_avatar',
            'wp_user_avatar_url',
            'simple_local_avatar',
        ];

        foreach ($meta_keys as $key) {
            $raw = get_user_meta((int) $user_id, $key, true);
            if (empty($raw)) {
                continue;
            }

            if (is_numeric($raw)) {
                $aid = (int) $raw;
                if ($aid > 0) {
                    $img = wp_get_attachment_image_url($aid, 'thumbnail');
                    if ($img) return (string) $img;
                    $img = wp_get_attachment_image_url($aid, 'full');
                    if ($img) return (string) $img;
                }
                continue;
            }

            if (is_array($raw)) {
                if (!empty($raw['media_id']) && is_numeric($raw['media_id'])) {
                    $aid = (int) $raw['media_id'];
                    $img = wp_get_attachment_image_url($aid, 'thumbnail');
                    if ($img) return (string) $img;
                    $img = wp_get_attachment_image_url($aid, 'full');
                    if ($img) return (string) $img;
                }
                foreach (['full', 'url', 'thumbnail', 'avatar'] as $k) {
                    if (!empty($raw[$k]) && is_string($raw[$k]) && filter_var($raw[$k], FILTER_VALIDATE_URL)) {
                        return (string) $raw[$k];
                    }
                }
                continue;
            }

            if (is_string($raw)) {
                $val = trim($raw);
                if ($val === '') {
                    continue;
                }
                if (ctype_digit($val)) {
                    $aid = (int) $val;
                    $img = wp_get_attachment_image_url($aid, 'thumbnail');
                    if ($img) return (string) $img;
                    $img = wp_get_attachment_image_url($aid, 'full');
                    if ($img) return (string) $img;
                    continue;
                }
                if (filter_var($val, FILTER_VALIDATE_URL)) {
                    return $val;
                }
            }
        }

        return $avatar !== '' ? $avatar : $default;
    }
}

/* ---------- SHORTCODE: hb_topbar_actions ---------- */
add_shortcode('hb_topbar_actions', function () {
    $role = hbx_role();
    if ($role === '' || $role === 'administrator') {
        return '';
    }

    $u = wp_get_current_user();

    $logout_url = wp_logout_url(home_url('/'));
    $profile_url = home_url('/profile/');
    $wallet_url = home_url('/wallet/');
    $friends_url = home_url('/amis/');
    $messages_url = home_url('/messages/');
    $notifications_url = home_url('/notif/');
    $help_url = home_url('/aide/');
    $search_url = home_url('/recherche/');

    $avatar_url = hbx_resolve_user_avatar_url((int) $u->ID, 80);

    $kpi_title = 'Compte';
    $kpi = ['label' => '—', 'ok' => false, 'hint' => ''];

    if ($role === 'prof') {
        $kpi_title = 'Salaire';
        $kpi = hbx_prof_salary_data($u);
    } elseif ($role === 'student') {
        $kpi_title = 'Points';
        $kpi = hbx_student_points_data($u);
    }

    $kpi_class = !empty($kpi['ok']) ? 'is-ok' : (hbx_debug_mode() ? 'is-ko' : 'is-neutral');
    $uid = hbx_unique_id('hbxA');

    ob_start(); ?>

<div class="hbxA-shell" id="<?php echo esc_attr($uid); ?>" data-api-base="<?php echo esc_attr(hbx_front_api_base()); ?>">
  <div class="hbxA-inner">
    <div class="hbxA-actions">
      <div class="hbxA-pill <?php echo esc_attr($kpi_class); ?>" title="<?php echo esc_attr($kpi['hint'] ?? ''); ?>">
        <span><?php echo esc_html($kpi_title); ?>:</span>
        <b><?php echo esc_html($kpi['label'] ?? '—'); ?></b>
      </div>

      <?php if ($role === 'student') : ?>
        <a class="hbxA-wallet" href="<?php echo esc_url($wallet_url); ?>">Wallet</a>
      <?php endif; ?>

      <form class="hbxA-search" action="<?php echo esc_url($search_url); ?>" method="get" role="search" aria-label="Recherche">
        <input class="hbxA-search-input" type="search" name="q" placeholder="Rechercher..." autocomplete="off">
        <button class="hbxA-search-btn" type="submit" aria-label="Rechercher">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm0 2a5 5 0 1 1 0 10 5 5 0 0 1 0-10Zm6.7 10.3 3 3-1.4 1.4-3-3 1.4-1.4Z" fill="currentColor"></path>
          </svg>
        </button>
      </form>

      <a class="hbxA-navbtn" href="<?php echo esc_url($friends_url); ?>" title="Amis" aria-label="Amis">
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm-7 9v-1a6 6 0 0 1 12 0v1H5Zm13-7a3 3 0 1 0-2.2-5 5.7 5.7 0 0 1 .5 2 5.8 5.8 0 0 1-.5 2 3 3 0 0 0 2.2 1Zm1 7v-1a6.8 6.8 0 0 0-1.5-4.2A5.9 5.9 0 0 1 22 20v1h-3Z" fill="currentColor"></path>
        </svg>
        <span>Amis</span>
      </a>

      <a class="hbxA-navbtn" href="<?php echo esc_url($messages_url); ?>" title="Messages" aria-label="Messages">
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M4 5h16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-5 4v-4H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Zm1 3v2h14V8H5Zm0 4v2h9v-2H5Z" fill="currentColor"></path>
        </svg>
        <span>Msg</span>
      </a>

      <div class="hbxA-quicklinks" aria-label="Raccourcis">
        <a class="hbxA-chip hbxA-chip-notif" href="<?php echo esc_url($notifications_url); ?>" title="Notifications" aria-label="Notifications">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 3a6 6 0 0 0-6 6v3.6l-1.2 2.1A1 1 0 0 0 5.7 16h12.6a1 1 0 0 0 .9-1.3L18 12.6V9a6 6 0 0 0-6-6Zm-2.2 14a2.2 2.2 0 0 0 4.4 0Z" fill="currentColor"></path>
          </svg>
          <span>Notif</span>
        </a>
        <a class="hbxA-chip hbxA-chip-help" href="<?php echo esc_url($help_url); ?>" title="Aide et support" aria-label="Aide et support">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2Zm0 17a1.2 1.2 0 1 1 1.2-1.2A1.2 1.2 0 0 1 12 19Zm1.5-5.3v.6h-3v-1.1c0-1.3.8-2 1.5-2.5.6-.5 1-.8 1-1.4a1.5 1.5 0 0 0-3 0H7a4.5 4.5 0 0 1 9 0c0 1.8-1.1 2.7-2 3.4-.3.3-.5.5-.5 1Z" fill="currentColor"></path>
          </svg>
          <span>Aide</span>
        </a>
      </div>

      <a class="hbxA-avatar" href="<?php echo esc_url($profile_url); ?>" title="Mon profil" aria-label="Mon profil">
        <img src="<?php echo esc_url($avatar_url); ?>" alt="Photo de profil" loading="lazy">
      </a>

      <a class="hbxA-logout" href="<?php echo esc_url($logout_url); ?>">Déconnexion</a>
    </div>
  </div>
</div>

<style>
:root{--hbx-bg:#fff;--hbx-text:#0f172a;--hbx-border:#e5e7eb;--hbx-hover:#f8fafc;--hbx-accent:#0E3A5D;--hbx-r:12px;--hbx-max:1320px;}
.hbxA-shell{position:sticky;top:0;z-index:2147483000!important;background:var(--hbx-bg);border-bottom:1px solid var(--hbx-border);overflow:visible;isolation:isolate;min-height:62px;}
.hbxA-inner{max-width:var(--hbx-max);margin:0 auto;padding:12px 18px;display:flex;justify-content:flex-end;overflow:visible;min-height:62px;}
.hbxA-actions{display:flex;align-items:center;gap:12px;flex-wrap:nowrap;justify-content:flex-end;overflow-x:auto;overflow-y:visible;scrollbar-width:none;}
.hbxA-actions::-webkit-scrollbar{display:none;}
.hbxA-pill{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:999px;border:1px solid var(--hbx-border);font-weight:700;background:#f8fafc;color:#0f172a;}
.hbxA-pill.is-ok{background:rgba(14,58,93,.06);border-color:rgba(14,58,93,.14);}
.hbxA-pill.is-neutral{background:#f8fafc;border-color:#d9e1eb;}
.hbxA-pill.is-ko{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.22);}
.hbxA-wallet{display:inline-flex;align-items:center;padding:9px 12px;border-radius:12px;border:1px solid rgba(14,58,93,.22);background:linear-gradient(180deg,#17466f,#0E3A5D);color:#fff;text-decoration:none;font-weight:900;}
.hbxA-wallet:hover{filter:brightness(1.08);}
.hbxA-search{display:inline-grid;grid-template-columns:1fr auto;align-items:center;border:1px solid #d9e1eb;border-radius:12px;background:#fff;overflow:hidden;min-width:220px;}
.hbxA-search-input{border:0;background:transparent;min-width:0;width:100%;padding:9px 10px;font-size:13px;color:#0f172a;outline:none;}
.hbxA-search-input::placeholder{color:#64748b;}
.hbxA-search-btn{width:36px;height:36px;border:0;border-left:1px solid #d9e1eb;background:#f8fafc;color:#0E3A5D;display:grid;place-items:center;cursor:pointer;}
.hbxA-search-btn:hover{background:#eef4fb;}
.hbxA-search-btn svg{width:17px;height:17px;display:block;}
.hbxA-navbtn{display:inline-flex;align-items:center;gap:7px;padding:9px 11px;border-radius:12px;border:1px solid var(--hbx-border);background:#fff;color:#0f172a;text-decoration:none;font-weight:800;line-height:1;}
.hbxA-navbtn:hover{background:var(--hbx-hover);}
.hbxA-navbtn svg{width:16px;height:16px;display:block;flex:0 0 auto;color:#0E3A5D;}
.hbxA-quicklinks{display:inline-flex;align-items:center;gap:8px;}
.hbxA-chip{display:inline-flex;align-items:center;gap:6px;padding:9px 12px;border-radius:999px;text-decoration:none;font-weight:800;font-size:12px;letter-spacing:.2px;line-height:1;transition:all .18s ease;}
.hbxA-chip svg{width:14px;height:14px;display:block;flex:0 0 auto;}
.hbxA-chip-help{color:#0b3f64;background:linear-gradient(180deg,#eef6ff,#e5f1ff);border:1px solid #cfe2f6;}
.hbxA-chip-notif{color:#92400e;background:linear-gradient(180deg,#fff7ed,#ffedd5);border:1px solid #fed7aa;}
.hbxA-chip:hover{transform:translateY(-1px);box-shadow:0 8px 18px rgba(15,23,42,.09);}
.hbxA-iconbtn{width:38px;height:38px;border-radius:12px;border:1px solid var(--hbx-border);background:transparent;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;position:relative;color:#0f172a;font-weight:900;}
.hbxA-iconbtn:hover{background:var(--hbx-hover);}
.hbxA-dot{position:absolute;top:5px;right:5px;width:8px;height:8px;border-radius:999px;background:#ef4444;border:1px solid #fff;}
.hbxA-avatar{width:38px;height:38px;border-radius:999px;overflow:hidden;border:1px solid var(--hbx-border);display:inline-flex;}
.hbxA-avatar img{width:100%;height:100%;object-fit:cover;display:block;}
.hbxA-logout{display:inline-flex;align-items:center;padding:9px 12px;border-radius:12px;border:1px solid var(--hbx-border);text-decoration:none;color:#0f172a;font-weight:900;}
.hbxA-logout:hover{background:var(--hbx-hover);}
.hbxA-popwrap{position:relative;z-index:2147483646;}
.hbxA-pop{position:absolute;right:0;top:calc(100% + 8px);width:340px;max-height:420px;overflow:auto;background:#fff;border:1px solid #dbe3ef;border-radius:14px;box-shadow:0 18px 42px rgba(2,6,23,.18);z-index:2147483647;}
.hbxA-pop-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 12px;border-bottom:1px solid #e9eef6;position:sticky;top:0;background:#fff;}
.hbxA-pop-body{padding:10px;display:flex;flex-direction:column;gap:8px;}
.hbxA-pop-close,.hbxA-mark-all{border:1px solid #d5deeb;background:#fff;border-radius:10px;padding:6px 8px;cursor:pointer;font-weight:800;}
.hbxA-pop-close:hover,.hbxA-mark-all:hover{background:#f8fafc;}
.hbxA-item{border:1px solid #dce5f0;border-radius:10px;padding:8px 9px;background:#fff;cursor:pointer;}
.hbxA-item:hover{background:#f8fbff;}
.hbxA-item h4{margin:0;font-size:13px;line-height:1.3;}
.hbxA-item p{margin:5px 0 0;font-size:12px;color:#475569;}
.hbxA-meta{margin-top:6px;font-size:11px;color:#64748b;}
.hbxA-unread{border-left:3px solid #0E3A5D;}
.hbxA-empty{border:1px dashed #dbe3ef;border-radius:10px;padding:10px;color:#64748b;font-size:12px;background:#fff;}
.hbxA-shell + .hbxM-shell{z-index:2147482990!important;isolation:isolate;}
#main-content, #et-main-area, .et_builder_inner_content{position:relative;z-index:1;}
@media (max-width:860px){
  .hbxA-search{min-width:180px;max-width:220px;}
  .hbxA-navbtn span{display:none;}
  .hbxA-chip{padding:8px 10px;font-size:11px;}
  .hbxA-pill{display:none;}
}
@media (max-width:520px){.hbxA-pop{width:min(92vw,340px);}}
</style>

<script>
(function(){
  const root = document.getElementById(<?php echo wp_json_encode($uid); ?>);
  if (!root) return;

  const helpBtn = root.querySelector('.hbxA-help-btn');
  const helpPop = root.querySelector('.hbxA-help-pop');
  const helpList = root.querySelector('.hbxA-help-list');
  const helpClose = root.querySelector('.hbxA-pop-close');

  const notifBtn = root.querySelector('.hbxA-notif-btn');
  const notifPop = root.querySelector('.hbxA-notif-pop');
  const notifList = root.querySelector('.hbxA-notif-list');
  const notifDot = root.querySelector('.hbxA-dot');
  const markAllBtn = root.querySelector('.hbxA-mark-all');

  if (!helpBtn || !helpPop || !helpList || !helpClose || !notifBtn || !notifPop || !notifList || !notifDot || !markAllBtn) return;

  function normalizeApiBase(raw){
    const b = String(raw || '').trim().replace(/\/+$/, '');
    if (!b) return '/api/v1';
    if (/\/api\/v1$/i.test(b)) return b;
    if (/\/api$/i.test(b)) return b + '/v1';
    if (/^https?:\/\//i.test(b) || b.startsWith('/')) return b + '/api/v1';
    return '/api/v1';
  }

  const API_BASE = normalizeApiBase((window.HB_CONFIG && window.HB_CONFIG.apiBase) ? window.HB_CONFIG.apiBase : (root.dataset.apiBase || ''));
  const TOKEN = String((window.HB_CONFIG && window.HB_CONFIG.token) ? window.HB_CONFIG.token : '').trim();

  let helpLoaded = false;

  function esc(s){
    return String(s == null ? '' : s)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/\"/g,'&quot;')
      .replace(/'/g,'&#039;');
  }

  function fmtDate(v){
    const d = new Date(v);
    if (String(d) === 'Invalid Date') return String(v || '');
    return d.toLocaleString('fr-FR', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
  }

  function openPop(p){ if (p) p.hidden = false; }
  function closePop(p){ if (p) p.hidden = true; }
  function closeAll(){ closePop(helpPop); closePop(notifPop); }

  function authHeaders(required){
    const h = {'Accept': 'application/json'};
    if (TOKEN) {
      h['Authorization'] = 'Bearer ' + TOKEN;
      h['X-HB-Token'] = TOKEN;
    }
    if (required && !TOKEN) {
      const e = new Error('Token JWT manquant');
      e.status = 401;
      throw e;
    }
    return h;
  }

  async function api(path, options, requiredAuth){
    const opts = options || {};
    const method = opts.method || 'GET';
    const headers = Object.assign({}, authHeaders(!!requiredAuth), opts.headers || {});

    const res = await fetch(API_BASE + path, {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body || undefined,
    });

    const text = await res.text().catch(() => '');
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch(_e) {}

    if (!res.ok) {
      const msg = (data && data.detail) ? data.detail : ('HTTP ' + res.status);
      const err = new Error(msg);
      err.status = res.status;
      const ra = parseInt(res.headers.get('Retry-After') || '', 10);
      if (Number.isFinite(ra) && ra > 0) err.retryAfterSec = ra;
      throw err;
    }

    return data;
  }

  async function apiGet(path, requiredAuth){
    return api(path, {method:'GET'}, requiredAuth);
  }

  async function apiPost(path, body, requiredAuth){
    return api(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    }, requiredAuth);
  }

  async function loadHelp(){
    helpList.innerHTML = '<div class="hbxA-empty">Chargement...</div>';
    try {
      const rows = await apiGet('/help/articles', false);
      const list = Array.isArray(rows) ? rows : [];
      if (!list.length) {
        helpList.innerHTML = '<div class="hbxA-empty">Aucun article.</div>';
        return;
      }
      helpList.innerHTML = list.map(a =>
        '<article class="hbxA-item"><h4>' + esc(a.title || 'Aide') + '</h4><p>' + esc(a.content || '') + '</p></article>'
      ).join('');
    } catch (e) {
      helpList.innerHTML = '<div class="hbxA-empty">Erreur aide: ' + esc(e.message || e) + '</div>';
    }
  }

  async function loadNotifications(){
    notifList.innerHTML = '<div class="hbxA-empty">Chargement...</div>';
    try {
      const rows = await apiGet('/notifications?limit=30', true);
      const list = Array.isArray(rows) ? rows : [];
      const unread = list.filter(n => !n.is_read).length;
      hbApplyNotifDot(unread);

      if (!list.length) {
        notifList.innerHTML = '<div class="hbxA-empty">Aucune notification.</div>';
        return;
      }

      notifList.innerHTML = list.map(n =>
        '<article class="hbxA-item ' + (n.is_read ? '' : 'hbxA-unread') + '" data-id="' + esc(n.id) + '" data-read="' + (n.is_read ? '1' : '0') + '" data-payload-url="' + esc((n.payload && n.payload.url) ? n.payload.url : '') + '">' +
          '<h4>' + esc(n.title || n.kind || 'Notification') + '</h4>' +
          '<p>' + esc(n.body || '') + '</p>' +
          '<div class="hbxA-meta">' + esc(fmtDate(n.created_at)) + '</div>' +
        '</article>'
      ).join('');
    } catch (e) {
      notifDot.hidden = true;
      notifList.innerHTML = '<div class="hbxA-empty">Notifications indisponibles: ' + esc(e.message || e) + '</div>';
    }
  }

  async function markOneRead(id){
    await apiPost('/notifications/' + encodeURIComponent(id) + '/read', {}, true);
  }

  async function markAllRead(){
    await apiPost('/notifications/read-all', {}, true);
  }

  helpBtn.addEventListener('click', async function(e){
    e.stopPropagation();
    const was = helpPop.hidden;
    closeAll();
    if (was) {
      openPop(helpPop);
      if (!helpLoaded) {
        helpLoaded = true;
        await loadHelp();
      }
    }
  });

  helpClose.addEventListener('click', function(e){
    e.stopPropagation();
    closePop(helpPop);
  });

  notifBtn.addEventListener('click', async function(e){
    e.stopPropagation();
    const was = notifPop.hidden;
    closeAll();
    if (was) {
      openPop(notifPop);
      if (!TOKEN) {
        notifList.innerHTML = '<div class="hbxA-empty">JWT manquant.</div>';
        return;
      }
      await loadNotifications();
    }
  });

  markAllBtn.addEventListener('click', async function(e){
    e.stopPropagation();
    try {
      await markAllRead();
      await loadNotifications();
    } catch (err) {
      notifList.innerHTML = '<div class="hbxA-empty">Erreur: ' + esc(err.message || err) + '</div>';
    }
  });

  notifList.addEventListener('click', async function(e){
    const item = e.target.closest('.hbxA-item[data-id]');
    if (!item) return;

    const id = item.getAttribute('data-id');
    const read = item.getAttribute('data-read') === '1';
    const payloadUrl = item.getAttribute('data-payload-url') || '';

    try {
      if (!read && id) await markOneRead(id);
    } catch (_e) {}

    if (payloadUrl) {
      const finalUrl = /^https?:\/\//i.test(payloadUrl) ? payloadUrl : (window.location.origin + payloadUrl);
      window.location.href = finalUrl;
    } else {
      await loadNotifications();
    }
  });

  document.addEventListener('click', function(e){
    if (!root.contains(e.target)) closeAll();
  });

  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape') closeAll();
  });

  // Anti-429: singleton polling + backoff + tab visible only
  window.__HBX_NOTIF_DOTS__ = window.__HBX_NOTIF_DOTS__ || [];
  if (!window.__HBX_NOTIF_DOTS__.includes(notifDot)) {
    window.__HBX_NOTIF_DOTS__.push(notifDot);
  }

  window.__HBX_NOTIF_POLL__ = window.__HBX_NOTIF_POLL__ || {
    timer: null,
    inFlight: false,
    started: false,
    delayMs: 120000,
    unread: 0
  };

  const HBX_NOTIF_POLL = window.__HBX_NOTIF_POLL__;

  function hbApplyNotifDot(unreadCount){
    (window.__HBX_NOTIF_DOTS__ || []).forEach((dot) => {
      if (dot) dot.hidden = unreadCount <= 0;
    });
  }

  function hbScheduleNextPoll(ms){
    if (HBX_NOTIF_POLL.timer) clearTimeout(HBX_NOTIF_POLL.timer);
    HBX_NOTIF_POLL.timer = setTimeout(hbSoftNotifRefresh, ms);
  }

  async function hbSoftNotifRefresh(){
    if (!TOKEN) {
      hbApplyNotifDot(0);
      return;
    }

    if (document.visibilityState === 'hidden') {
      hbScheduleNextPoll(HBX_NOTIF_POLL.delayMs);
      return;
    }

    if (HBX_NOTIF_POLL.inFlight) {
      hbScheduleNextPoll(HBX_NOTIF_POLL.delayMs);
      return;
    }

    HBX_NOTIF_POLL.inFlight = true;

    try {
      const rows = await apiGet('/notifications?limit=15', true);
      const list = Array.isArray(rows) ? rows : [];
      HBX_NOTIF_POLL.unread = list.filter(n => !n.is_read).length;
      HBX_NOTIF_POLL.delayMs = 120000;
      hbApplyNotifDot(HBX_NOTIF_POLL.unread);
    } catch (e) {
      if (e && e.status === 429) {
        if (e.retryAfterSec) {
          HBX_NOTIF_POLL.delayMs = Math.min(Math.max(e.retryAfterSec * 1000, 120000), 15 * 60 * 1000);
        } else {
          HBX_NOTIF_POLL.delayMs = Math.min(HBX_NOTIF_POLL.delayMs * 2, 15 * 60 * 1000);
        }
      } else {
        HBX_NOTIF_POLL.delayMs = Math.min(Math.max(HBX_NOTIF_POLL.delayMs, 120000), 5 * 60 * 1000);
      }
      hbApplyNotifDot(HBX_NOTIF_POLL.unread);
    } finally {
      HBX_NOTIF_POLL.inFlight = false;
      hbScheduleNextPoll(HBX_NOTIF_POLL.delayMs);
    }
  }

  document.addEventListener('visibilitychange', function(){
    if (document.visibilityState === 'visible') {
      hbScheduleNextPoll(1000);
    }
  });

  hbApplyNotifDot(HBX_NOTIF_POLL.unread);
  if (!HBX_NOTIF_POLL.started) {
    HBX_NOTIF_POLL.started = true;
    hbScheduleNextPoll(5000);
  }
})();
</script>

<?php
    return (string) ob_get_clean();
});

/* ---------- SHORTCODE: hb_topbar_menu ---------- */
add_shortcode('hb_topbar_menu', function () {
    $role = hbx_role();
    if ($role === '' || $role === 'administrator') {
        return '';
    }

    $menu_name = hbx_menu_for_role($role);
    if ($menu_name === '') {
        return '';
    }

    $uid = hbx_unique_id('hbxM');

    $menu_html = wp_nav_menu([
        'menu'        => $menu_name,
        'container'   => false,
        'echo'        => false,
        'depth'       => 2,
        'fallback_cb' => '__return_empty_string',
        'menu_class'  => 'hbxM-menu',
        'items_wrap'  => '<ul class="%2$s">%3$s</ul>',
    ]);

    if (!is_string($menu_html) || trim($menu_html) === '') {
        return '';
    }

    ob_start(); ?>

<div class="hbxM-shell" id="<?php echo esc_attr($uid); ?>">
  <div class="hbxM-inner">
    <nav class="hbxM-nav" aria-label="Navigation"><?php echo $menu_html; ?></nav>
  </div>
</div>

<style>
:root{--hbxM-bg:#fff;--hbxM-text:#0f172a;--hbxM-border:#e5e7eb;--hbxM-accent:#0E3A5D;--hbxM-max:1320px;}
.hbxM-shell{position:sticky;top:62px;z-index:2147482990!important;background:var(--hbxM-bg);border-bottom:1px solid var(--hbxM-border);isolation:isolate;overflow:visible;}
.hbxM-inner{max-width:var(--hbxM-max);margin:0 auto;padding:0 18px;}
.hbxM-menu{display:flex!important;align-items:center;gap:24px;margin:0!important;padding:0 4px!important;list-style:none!important;overflow:auto;scrollbar-width:none;white-space:nowrap;}
.hbxM-menu::-webkit-scrollbar{display:none;}
.hbxM-menu>li{margin:0!important;padding:0!important;}
.hbxM-menu>li:before,.hbxM-menu>li:after{display:none!important;content:none!important;}
.hbxM-menu>li>a{display:inline-flex!important;align-items:center;padding:14px 0!important;text-decoration:none!important;color:var(--hbxM-text)!important;font-weight:900!important;border-bottom:3px solid transparent!important;opacity:.86;}
.hbxM-menu>li>a:hover{opacity:1;}
.hbxM-menu>li.current-menu-item>a,.hbxM-menu>li.current_page_item>a{opacity:1;border-bottom-color:var(--hbxM-accent)!important;}
.hbxM-menu .sub-menu{display:none!important;}
</style>

<?php
    return (string) ob_get_clean();
});
