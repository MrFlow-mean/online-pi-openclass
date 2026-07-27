#!/bin/sh
set -eu

/usr/bin/answer init
/usr/bin/answer upgrade

case "${OPENCLASS_ANSWER_THEME_ENABLED:-true}" in
  true|TRUE|1|yes|YES)
    openclass_home_url="${OPENCLASS_HOME_URL:-http://127.0.0.1:3000/home}"
    case "$openclass_home_url" in
      http://*|https://*) ;;
      *)
        echo "OPENCLASS_HOME_URL must use http:// or https://" >&2
        exit 1
        ;;
    esac
    case "$openclass_home_url" in
      *\"*|*\'*|*\<*|*\>*|*\\*|*" "*)
        echo "OPENCLASS_HOME_URL contains unsupported characters" >&2
        exit 1
        ;;
    esac
    case "$openclass_home_url" in
      */home) openclass_home_origin="${openclass_home_url%/home}" ;;
      */home/) openclass_home_origin="${openclass_home_url%/home/}" ;;
      *) openclass_home_origin="${openclass_home_url%/}" ;;
    esac
    openclass_favicon_url="${OPENCLASS_FAVICON_URL:-$openclass_home_origin/favicon.ico}"
    case "$openclass_favicon_url" in
      http://*|https://*) ;;
      *)
        echo "OPENCLASS_FAVICON_URL must use http:// or https://" >&2
        exit 1
        ;;
    esac
    case "$openclass_favicon_url" in
      *\"*|*\'*|*\<*|*\>*|*\\*|*" "*)
        echo "OPENCLASS_FAVICON_URL contains unsupported characters" >&2
        exit 1
        ;;
    esac
    openclass_theme_stylesheet_url="/custom.css"
    if [ -n "${OPENCLASS_COMMUNITY_PUBLIC_URL:-}" ]; then
      case "$OPENCLASS_COMMUNITY_PUBLIC_URL" in
        http://*|https://*) ;;
        *)
          echo "OPENCLASS_COMMUNITY_PUBLIC_URL must use http:// or https://" >&2
          exit 1
          ;;
      esac
      case "$OPENCLASS_COMMUNITY_PUBLIC_URL" in
        *\"*|*\'*|*\<*|*\>*|*\\*|*" "*)
          echo "OPENCLASS_COMMUNITY_PUBLIC_URL contains unsupported characters" >&2
          exit 1
          ;;
      esac
      openclass_theme_stylesheet_url="${OPENCLASS_COMMUNITY_PUBLIC_URL%/}/custom.css"
    fi
    openclass_community_entry_url="$openclass_home_origin/community"

    sqlite3 /data/answer.db \
      -cmd ".parameter init" \
      -cmd ".parameter set @openclass_home_url \"$openclass_home_url\"" \
      -cmd ".parameter set @openclass_favicon_url \"$openclass_favicon_url\"" \
      -cmd ".parameter set @openclass_theme_stylesheet_url \"$openclass_theme_stylesheet_url\"" \
      -cmd ".parameter set @openclass_community_entry_url \"$openclass_community_entry_url\"" <<'SQL'
BEGIN IMMEDIATE;
DELETE FROM site_info WHERE type IN ('css-html', 'custom_css_html');
INSERT INTO site_info (created_at, updated_at, type, content, status)
VALUES (
  datetime('now'),
  datetime('now'),
  'css-html',
  json_object(
    'custom_head', '<link rel="stylesheet" href="' || @openclass_theme_stylesheet_url || '">' ||
      '<script>window.__OPENCLASS_COMMUNITY_BRIDGE__={entryUrl:"' || @openclass_community_entry_url || '"};</script>' ||
      '<script>' || CAST(readfile('/opt/openclass/openclass-sso-bridge.js') AS TEXT) || '</script>',
    'custom_css', CAST(readfile('/opt/openclass/openclass-theme.css') AS TEXT),
    'custom_header', '<a class="openclass-home-link" href="' || @openclass_home_url || '" aria-label="返回 OpenClass 主页"><span aria-hidden="true">←</span><span>返回主页</span></a>',
    'custom_footer', '',
    'custom_sidebar', ''
  ),
  1
);
INSERT INTO site_info (created_at, updated_at, type, content, status)
SELECT
  datetime('now'),
  datetime('now'),
  'branding',
  json_object('logo', '', 'mobile_logo', '', 'square_icon', '', 'favicon', @openclass_favicon_url),
  1
WHERE NOT EXISTS (SELECT 1 FROM site_info WHERE type = 'branding');
UPDATE site_info
SET content = json_set(content, '$.favicon', @openclass_favicon_url),
    updated_at = datetime('now')
WHERE type = 'branding';
UPDATE site_info
SET content = json_set(
      content,
      '$.theme_config.default.navbar_style', '#f7f5ef',
      '$.theme_config.default.primary_color', '#11100e',
      '$.layout', 'Full-width'
    ),
    updated_at = datetime('now')
WHERE type = 'theme';
UPDATE site_info
SET content = json_set(content, '$.name', 'OpenClass 学习社区'),
    updated_at = datetime('now')
WHERE type = 'general'
  AND json_extract(content, '$.name') = 'OpenClass Learning Community';
UPDATE site_info
SET content = json_set(content, '$.default_avatar', 'system'),
    updated_at = datetime('now')
WHERE type IN ('users_settings', 'users');
COMMIT;
SQL
    rm -f /data/cache/cache.db
    ;;
esac

oauth_client_id="${OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID:-}"
oauth_client_secret="${OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET:-}"
if [ -n "$oauth_client_id" ] || [ -n "$oauth_client_secret" ]; then
  if [ -z "$oauth_client_id" ] || [ -z "$oauth_client_secret" ]; then
    echo "OPENCLASS_COMMUNITY_OAUTH_CLIENT_ID and OPENCLASS_COMMUNITY_OAUTH_CLIENT_SECRET must be configured together" >&2
    exit 1
  fi

  community_public_url="${OPENCLASS_COMMUNITY_PUBLIC_URL:-${OPENCLASS_ANSWER_SITE_URL:-}}"
  case "$community_public_url" in
    http://*|https://*) ;;
    *)
      echo "OPENCLASS_COMMUNITY_PUBLIC_URL must use http:// or https:// when Answer SSO is configured" >&2
      exit 1
      ;;
  esac
  case "$community_public_url$oauth_client_id" in
    *\"*|*\'*|*\<*|*\>*|*\\*|*" "*)
      echo "Answer SSO URLs and client ID contain unsupported characters" >&2
      exit 1
      ;;
  esac

  case "${OPENCLASS_HOME_URL:-}" in
    */home) openclass_origin="${OPENCLASS_HOME_URL%/home}" ;;
    */home/) openclass_origin="${OPENCLASS_HOME_URL%/home/}" ;;
    http://*|https://*) openclass_origin="${OPENCLASS_HOME_URL%/}" ;;
    *) openclass_origin="${community_public_url%/}" ;;
  esac
  oauth_authorize_url="${OPENCLASS_COMMUNITY_OAUTH_AUTHORIZE_URL:-$openclass_origin/api/auth/community/authorize}"
  oauth_token_url="${OPENCLASS_COMMUNITY_OAUTH_TOKEN_URL:-$openclass_origin/api/auth/community/token}"
  oauth_userinfo_url="${OPENCLASS_COMMUNITY_OAUTH_USERINFO_URL:-$openclass_origin/api/auth/community/userinfo}"
  for oauth_url in "$oauth_authorize_url" "$oauth_token_url" "$oauth_userinfo_url"; do
    case "$oauth_url" in
      http://*|https://*) ;;
      *)
        echo "Answer SSO endpoint URLs must use http:// or https://" >&2
        exit 1
        ;;
    esac
    case "$oauth_url" in
      *\"*|*\'*|*\<*|*\>*|*\\*|*" "*)
        echo "Answer SSO endpoint URLs contain unsupported characters" >&2
        exit 1
        ;;
    esac
  done

  umask 077
  oauth_secret_file="/tmp/openclass-answer-oauth-secret.$$"
  printf '%s' "$oauth_client_secret" > "$oauth_secret_file"
  trap 'rm -f "$oauth_secret_file"' EXIT INT TERM
  sqlite3 /data/answer.db \
    -cmd ".parameter init" \
    -cmd ".parameter set @community_public_url \"$community_public_url\"" \
    -cmd ".parameter set @oauth_client_id \"$oauth_client_id\"" \
    -cmd ".parameter set @oauth_secret_file \"$oauth_secret_file\"" \
    -cmd ".parameter set @oauth_authorize_url \"$oauth_authorize_url\"" \
    -cmd ".parameter set @oauth_token_url \"$oauth_token_url\"" \
    -cmd ".parameter set @oauth_userinfo_url \"$oauth_userinfo_url\"" <<'SQL'
BEGIN IMMEDIATE;
INSERT INTO plugin_config (plugin_slug_name, value)
VALUES (
  'basic_connector',
  json_object(
    'authorize_url', @oauth_authorize_url,
    'check_email_verified', json('false'),
    'client_id', @oauth_client_id,
    'client_secret', CAST(readfile(@oauth_secret_file) AS TEXT),
    'email_verified_json_path', 'email_verified',
    'logo_svg', '',
    'name', 'OpenClass',
    'scope', '',
    'token_url', @oauth_token_url,
    'user_avatar_json_path', 'avatar_url',
    'user_display_name_json_path', 'name',
    'user_email_json_path', 'email',
    'user_id_json_path', 'id',
    'user_json_url', @oauth_userinfo_url,
    'user_username_json_path', 'username'
  )
)
ON CONFLICT(plugin_slug_name) DO UPDATE SET value = excluded.value;
INSERT INTO config (key, value)
VALUES ('plugin.status', json_object('basic_connector', json('true')))
ON CONFLICT(key) DO UPDATE SET value = json_set(
  CASE WHEN json_valid(config.value) THEN config.value ELSE '{}' END,
  '$.basic_connector',
  json('true')
);
UPDATE site_info
SET content = json_set(content, '$.site_url', @community_public_url),
    updated_at = datetime('now')
WHERE type = 'general';
UPDATE site_info
SET content = json_set(
      content,
      '$.allow_new_registrations', json('true'),
      '$.allow_email_registrations', json('false'),
      '$.allow_password_login', json('false')
    ),
    updated_at = datetime('now')
WHERE type = 'login';
COMMIT;
SQL
  rm -f "$oauth_secret_file" /data/cache/cache.db
  trap - EXIT INT TERM
fi

exec /usr/bin/answer run -C /data/
