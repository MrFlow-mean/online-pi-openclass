#!/bin/zsh

set -euo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 /absolute/path/to/openclass-worktree" >&2
  exit 64
fi

TARGET_INPUT="$1"
LAUNCH_DIR="${OPENCLASS_LAUNCH_DIR:-$HOME/.openclass-launch}"
LAUNCH_AGENTS_DIR="${OPENCLASS_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
RUNTIME_CONFIG_DIR="${OPENCLASS_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/openclass}"
RUNTIME_ENV_FILE="${OPENCLASS_ENV_FILE:-$RUNTIME_CONFIG_DIR/runtime.env}"
API_LABEL="com.openclass.api"
WEB_LABEL="com.openclass.web"
API_PLIST="$LAUNCH_AGENTS_DIR/$API_LABEL.plist"
WEB_PLIST="$LAUNCH_AGENTS_DIR/$WEB_LABEL.plist"
HEALTH_TIMEOUT_SECONDS="${OPENCLASS_RUNTIME_HEALTH_TIMEOUT_SECONDS:-180}"
USER_DOMAIN="gui/$(id -u)"

log() {
  echo "[openclass-runtime] $*"
}

fail() {
  log "$*" >&2
  exit 1
}

resolve_directory() {
  local directory="$1"
  [[ -d "$directory" ]] || return 1
  (cd "$directory" && pwd -P)
}

validate_target() {
  local target="$1"
  local git_root

  [[ -f "$target/package.json" ]] || fail "Target is missing package.json: $target"
  [[ -f "$target/apps/api/app/main.py" ]] || fail "Target is missing the API application: $target"
  [[ -f "$target/apps/web/package.json" ]] || fail "Target is missing the web application: $target"
  git_root="$(git -C "$target" rev-parse --show-toplevel 2>/dev/null || true)"
  [[ -n "$git_root" ]] || fail "Target is not a Git worktree: $target"
  [[ "$(resolve_directory "$git_root")" == "$target" ]] || fail "Target must be the worktree root: $target"
}

stop_agents() {
  launchctl bootout "$USER_DOMAIN/$WEB_LABEL" >/dev/null 2>&1 || true
  launchctl bootout "$USER_DOMAIN/$API_LABEL" >/dev/null 2>&1 || true
  # Avoid invalidating a still-exiting shell's working directory when the link changes.
  sleep 1
}

start_agents() {
  launchctl bootstrap "$USER_DOMAIN" "$API_PLIST"
  launchctl bootstrap "$USER_DOMAIN" "$WEB_PLIST"
  launchctl enable "$USER_DOMAIN/$API_LABEL"
  launchctl enable "$USER_DOMAIN/$WEB_LABEL"
  launchctl kickstart -k "$USER_DOMAIN/$API_LABEL"
  launchctl kickstart -k "$USER_DOMAIN/$WEB_LABEL"
}

replace_launch_link() {
  local target="$1"

  if [[ -e "$LAUNCH_DIR" && ! -L "$LAUNCH_DIR" ]]; then
    fail "Launch path exists and is not a symbolic link: $LAUNCH_DIR"
  fi
  [[ ! -L "$LAUNCH_DIR" ]] || unlink "$LAUNCH_DIR"
  ln -s "$target" "$LAUNCH_DIR"
}

runtime_env_value() {
  local key="$1"

  awk -F= -v key="$key" '
    $1 == key {
      sub(/^[^=]*=/, "")
      gsub(/^[[:space:]"]+|[[:space:]"]+$/, "")
      print
      exit
    }
  ' "$RUNTIME_ENV_FILE"
}

required_auth_providers() {
  if [[ -n "${OPENCLASS_REQUIRED_AUTH_PROVIDERS:-}" ]]; then
    printf "%s" "$OPENCLASS_REQUIRED_AUTH_PROVIDERS"
    return
  fi
  runtime_env_value OPENCLASS_REQUIRED_AUTH_PROVIDERS
}

allow_auth_provider_removal() {
  if [[ -n "${OPENCLASS_ALLOW_AUTH_PROVIDER_REMOVAL:-}" ]]; then
    printf "%s" "$OPENCLASS_ALLOW_AUTH_PROVIDER_REMOVAL"
    return
  fi
  runtime_env_value OPENCLASS_ALLOW_AUTH_PROVIDER_REMOVAL
}

provider_ids() {
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/api/auth/providers \
    | python3 -c 'import json, sys; print("\n".join(item["id"] for item in json.load(sys.stdin)))'
}

verify_runtime() {
  local current_epoch="$(date +%s)"
  local deadline=$(( current_epoch + HEALTH_TIMEOUT_SECONDS ))
  local providers=""
  local required
  local provider

  while (( current_epoch < deadline )); do
    if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1 \
      && curl --fail --silent --show-error --max-time 5 http://127.0.0.1:3000/login >/dev/null 2>&1; then
      providers="$(provider_ids 2>/dev/null || true)"
      [[ -n "$providers" ]] && break
    fi
    sleep 2
    current_epoch="$(date +%s)"
  done

  [[ -n "$providers" ]] || return 1
  if [[ "$(allow_auth_provider_removal)" != "true" ]]; then
    for provider in ${(f)PREVIOUS_PROVIDERS}; do
      [[ -z "$provider" ]] && continue
      if ! grep -Fxq "$provider" <<<"$providers"; then
        log "Previously available auth provider disappeared: $provider" >&2
        return 1
      fi
    done
  fi

  required="$(required_auth_providers)"
  for provider in ${(s:,:)required}; do
    provider="${provider//[[:space:]]/}"
    [[ -z "$provider" ]] && continue
    if ! grep -Fxq "$provider" <<<"$providers"; then
      log "Required auth provider disappeared: $provider" >&2
      return 1
    fi
  done
}

TARGET="$(resolve_directory "$TARGET_INPUT" || true)"
[[ -n "$TARGET" ]] || fail "Target directory does not exist: $TARGET_INPUT"
validate_target "$TARGET"
[[ -f "$RUNTIME_ENV_FILE" ]] || fail "Runtime config is missing: $RUNTIME_ENV_FILE"
[[ -f "$API_PLIST" ]] || fail "API LaunchAgent is missing: $API_PLIST"
[[ -f "$WEB_PLIST" ]] || fail "Web LaunchAgent is missing: $WEB_PLIST"

PREVIOUS_TARGET=""
if [[ -L "$LAUNCH_DIR" ]]; then
  PREVIOUS_TARGET="$(resolve_directory "$LAUNCH_DIR" || true)"
fi
PREVIOUS_PROVIDERS="$(provider_ids 2>/dev/null || true)"

rollback() {
  local exit_code=$?
  trap - EXIT
  if (( exit_code != 0 )) && [[ -n "$PREVIOUS_TARGET" ]]; then
    set +e
    log "Verification failed; restoring $PREVIOUS_TARGET"
    stop_agents
    if [[ "$(resolve_directory "$LAUNCH_DIR" || true)" != "$PREVIOUS_TARGET" ]]; then
      replace_launch_link "$PREVIOUS_TARGET"
    fi
    start_agents
  fi
  exit "$exit_code"
}

trap rollback EXIT

log "Stopping services before switching the runtime"
stop_agents
replace_launch_link "$TARGET"
log "Starting services from $TARGET"
start_agents

if ! verify_runtime; then
  fail "Runtime health or capability verification failed"
fi

trap - EXIT
log "Runtime switch verified: $TARGET"
