#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_DIR="$HOME/.openclass-launch"
LAUNCH_PARENT="$(dirname "$LAUNCH_DIR")"
LAUNCH_BIN_DIR="$HOME/.openclass-launch-bin"
RUNTIME_CONFIG_DIR="${OPENCLASS_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/openclass}"
RUNTIME_ENV_FILE="${OPENCLASS_ENV_FILE:-$RUNTIME_CONFIG_DIR/runtime.env}"
RUNTIME_REVISION_FILE="${OPENCLASS_SOURCE_REVISION_FILE:-$RUNTIME_CONFIG_DIR/source-revision}"
WEB_RUNNER="$LAUNCH_BIN_DIR/keep-web-up.sh"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
WEB_LABEL="com.openclass.web"
API_LABEL="com.openclass.api"
WEB_TEMPLATE="$PROJECT_DIR/launchd/$WEB_LABEL.plist"
API_TEMPLATE="$PROJECT_DIR/launchd/$API_LABEL.plist"
WEB_TARGET="$LAUNCH_AGENTS_DIR/$WEB_LABEL.plist"
API_TARGET="$LAUNCH_AGENTS_DIR/$API_LABEL.plist"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$LAUNCH_BIN_DIR"
mkdir -p "$(dirname "$RUNTIME_ENV_FILE")"
mkdir -p "$(dirname "$RUNTIME_REVISION_FILE")"
if [[ ! -e "$RUNTIME_ENV_FILE" ]]; then
  if [[ -f "$PROJECT_DIR/.env" ]]; then
    install -m 600 "$PROJECT_DIR/.env" "$RUNTIME_ENV_FILE"
  else
    install -m 600 /dev/null "$RUNTIME_ENV_FILE"
  fi
else
  chmod 600 "$RUNTIME_ENV_FILE"
fi
SOURCE_REVISION="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
install -m 600 /dev/null "$RUNTIME_REVISION_FILE"
printf "%s\n" "$SOURCE_REVISION" > "$RUNTIME_REVISION_FILE"
if [[ -e "$LAUNCH_DIR" && ! -L "$LAUNCH_DIR" ]]; then
  echo "Launch path exists and is not a symbolic link: $LAUNCH_DIR" >&2
  exit 1
fi
launchctl bootout "gui/$(id -u)/$WEB_LABEL" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)/$API_LABEL" >/dev/null 2>&1 || true
# launchctl returns before the old shells have always released their working directory.
sleep 1
[[ ! -L "$LAUNCH_DIR" ]] || unlink "$LAUNCH_DIR"
ln -s "$PROJECT_DIR" "$LAUNCH_DIR"
cp "$PROJECT_DIR/scripts/keep-web-up.sh" "$WEB_RUNNER"
chmod +x "$WEB_RUNNER"

runtime_path() {
  local path_value="${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
  local tool_path
  local tool_dir

  for tool_path in "$(command -v node 2>/dev/null || true)" "$(command -v npm 2>/dev/null || true)"; do
    [[ -n "$tool_path" ]] || continue
    tool_dir="${tool_path:h}"
    if [[ ":$path_value:" != *":$tool_dir:"* ]]; then
      path_value="$tool_dir:$path_value"
    fi
  done

  printf "%s" "$path_value"
}

RUNTIME_PATH="$(runtime_path)"

install_agent() {
  local label="$1"
  local template="$2"
  local target="$3"

  sed \
    -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
    -e "s#__LAUNCH_DIR__#$LAUNCH_DIR#g" \
    -e "s#__LAUNCH_PARENT__#$LAUNCH_PARENT#g" \
    -e "s#__WEB_RUNNER__#$WEB_RUNNER#g" \
    -e "s#__OPENCLASS_ENV_FILE__#$RUNTIME_ENV_FILE#g" \
    -e "s#__OPENCLASS_SOURCE_REVISION_FILE__#$RUNTIME_REVISION_FILE#g" \
    -e "s#__OPENCLASS_RUNTIME_PATH__#$RUNTIME_PATH#g" \
    "$template" > "$target"
  launchctl bootstrap "gui/$(id -u)" "$target"
  launchctl enable "gui/$(id -u)/$label"
  launchctl kickstart -k "gui/$(id -u)/$label"
}

install_agent "$WEB_LABEL" "$WEB_TEMPLATE" "$WEB_TARGET"
install_agent "$API_LABEL" "$API_TEMPLATE" "$API_TARGET"
