#!/bin/zsh

set -uo pipefail

PROJECT_DIR="${OPENCLASS_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOG_PREFIX="[openclass-web]"
BUILD_DIR="$PROJECT_DIR/apps/web/.next"
BUILD_ID="$BUILD_DIR/BUILD_ID"
STAMP="$BUILD_DIR/openclass-source-stamp"

cd "$PROJECT_DIR"

export NEXT_TELEMETRY_DISABLED=1
export NODE_ENV=production

log() {
  echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') $*"
}

source_revision() {
  local revision_file="${OPENCLASS_SOURCE_REVISION_FILE:-$HOME/.config/openclass/source-revision}"
  local revision_reader="$PROJECT_DIR/scripts/read-source-revision.sh"
  local revision=""

  if [[ -f "$revision_file" ]]; then
    IFS= read -r revision < "$revision_file" || true
    if [[ -n "$revision" && ${#revision} -ge 7 && "$revision" != *[^0-9a-fA-F]* ]]; then
      printf "%s" "$revision"
      return
    fi
  fi

  if [[ -x "$revision_reader" ]]; then
    "$revision_reader" "$PROJECT_DIR" 2>/dev/null || printf "nogit"
    return
  fi
  git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || printf "nogit"
}

first_changed_source_after_build() {
  [[ -f "$BUILD_ID" ]] || return 1

  local source_paths=(
    "$PROJECT_DIR/apps/web/src"
    "$PROJECT_DIR/apps/web/public"
    "$PROJECT_DIR/apps/web/package.json"
    "$PROJECT_DIR/apps/web/next.config.js"
    "$PROJECT_DIR/apps/web/next.config.mjs"
    "$PROJECT_DIR/apps/web/next.config.ts"
    "$PROJECT_DIR/apps/web/tsconfig.json"
    "$PROJECT_DIR/package.json"
    "$PROJECT_DIR/package-lock.json"
  )
  local source_path
  local changed_file

  for source_path in "${source_paths[@]}"; do
    [[ -e "$source_path" ]] || continue
    changed_file="$(find "$source_path" -type f -newer "$BUILD_ID" -print -quit 2>/dev/null || true)"
    if [[ -n "$changed_file" ]]; then
      printf "%s" "$changed_file"
      return 0
    fi
  done

  return 1
}

validate_static_assets() {
  [[ -f "$BUILD_ID" ]] || return 1
  [[ -d "$BUILD_DIR/server" ]] || return 1
}

build_refresh_reason() {
  local head_sha
  local changed_file
  head_sha="$(source_revision)"

  if [[ ! -f "$BUILD_ID" ]]; then
    printf "missing production build"
    return
  fi
  if [[ ! -f "$STAMP" ]]; then
    printf "missing source stamp"
    return
  fi
  if [[ "$(cat "$STAMP" 2>/dev/null || true)" != "$head_sha" ]]; then
    printf "source revision changed"
    return
  fi
  changed_file="$(first_changed_source_after_build || true)"
  if [[ -n "$changed_file" ]]; then
    printf "source file changed after build: %s" "${changed_file#$PROJECT_DIR/}"
    return
  fi
  if ! validate_static_assets >/tmp/openclass-web-asset-check.log 2>&1; then
    printf "static asset manifest mismatch"
    return
  fi
}

release_port() {
  local raw
  local pids
  raw="$(lsof -tiTCP:3000 -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$raw" ]]; then
    return
  fi

  pids=("${(@f)raw}")
  if (( ${#pids[@]} == 0 )); then
    return
  fi

  log "stopping stale process on :3000: ${pids[*]}"
  kill "${pids[@]}" >/dev/null 2>&1 || true
  sleep 1

  raw="$(lsof -tiTCP:3000 -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$raw" ]]; then
    return
  fi

  pids=("${(@f)raw}")
  if (( ${#pids[@]} > 0 )); then
    log "force-stopping stale process on :3000: ${pids[*]}"
    kill -9 "${pids[@]}" >/dev/null 2>&1 || true
  fi
}

rebuild_web() {
  local reason="$1"
  local head_sha
  head_sha="$(source_revision)"

  log "$reason; rebuilding web"
  release_port
  rm -rf "$BUILD_DIR" 2>/dev/null || true
  if ! npm run build:web; then
    log "web build failed; retrying in 5s"
    sleep 5
    return 1
  fi
  mkdir -p "$BUILD_DIR"
  printf "%s\n" "$head_sha" > "$STAMP"
  if ! validate_static_assets >/tmp/openclass-web-asset-check.log 2>&1; then
    log "web build produced missing static assets; see /tmp/openclass-web-asset-check.log"
    rm -rf "$BUILD_DIR" 2>/dev/null || true
    sleep 5
    return 1
  fi
}

ensure_build_current() {
  local reason
  reason="$(build_refresh_reason)"
  if [[ -n "$reason" ]]; then
    rebuild_web "$reason"
    return $?
  fi
  log "production build is current"
}

stop_server_tree() {
  local server_pid="$1"
  local children

  children="$(pgrep -P "$server_pid" 2>/dev/null || true)"
  if [[ -n "$children" ]]; then
    kill ${=children} >/dev/null 2>&1 || true
  fi
  kill "$server_pid" >/dev/null 2>&1 || true
  sleep 1

  children="$(pgrep -P "$server_pid" 2>/dev/null || true)"
  if [[ -n "$children" ]]; then
    kill -9 ${=children} >/dev/null 2>&1 || true
  fi
  kill -9 "$server_pid" >/dev/null 2>&1 || true
}

while true; do
  if ! ensure_build_current; then
    continue
  fi

  release_port
  log "starting Next.js production server on :3000"
  npm --prefix apps/web run start &
  server_pid=$!

  while kill -0 "$server_pid" >/dev/null 2>&1; do
    sleep 5
    reason="$(build_refresh_reason)"
    if [[ -n "$reason" ]]; then
      log "$reason while server is running; restarting web"
      stop_server_tree "$server_pid"
      break
    fi
  done

  wait "$server_pid" >/dev/null 2>&1 || exit_code=$?
  log "server exited with code ${exit_code:-0}; restarting in 2s"
  unset exit_code
  sleep 2
done
