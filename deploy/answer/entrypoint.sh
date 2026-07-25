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

    sqlite3 /data/answer.db \
      -cmd ".parameter init" \
      -cmd ".parameter set @openclass_home_url \"$openclass_home_url\"" <<'SQL'
BEGIN IMMEDIATE;
DELETE FROM site_info WHERE type IN ('css-html', 'custom_css_html');
INSERT INTO site_info (created_at, updated_at, type, content, status)
VALUES (
  datetime('now'),
  datetime('now'),
  'css-html',
  json_object(
    'custom_head', '',
    'custom_css', CAST(readfile('/opt/openclass/openclass-theme.css') AS TEXT),
    'custom_header', '<a class="openclass-home-link" href="' || @openclass_home_url || '" aria-label="返回 OpenClass 主页"><span aria-hidden="true">←</span><span>返回主页</span></a>',
    'custom_footer', '',
    'custom_sidebar', ''
  ),
  1
);
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

exec /usr/bin/answer run -C /data/
