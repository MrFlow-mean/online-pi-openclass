#!/bin/zsh

set -u

if (( $# != 1 )); then
  echo "Usage: $0 /absolute/path/to/git-worktree" >&2
  exit 64
fi

PROJECT_DIR="$(cd "$1" 2>/dev/null && pwd -P)" || exit 1

if command -v git >/dev/null 2>&1; then
  revision="$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "$revision" ]]; then
    printf "%s" "$revision"
    exit 0
  fi
fi

GIT_MARKER="$PROJECT_DIR/.git"
if [[ -d "$GIT_MARKER" ]]; then
  GIT_DIR="$(cd "$GIT_MARKER" 2>/dev/null && pwd -P)" || exit 1
elif [[ -f "$GIT_MARKER" ]]; then
  IFS= read -r marker_value < "$GIT_MARKER" || exit 1
  [[ "$marker_value" == "gitdir: "* ]] || exit 1
  git_dir_value="${marker_value#gitdir: }"
  if [[ "$git_dir_value" == /* ]]; then
    GIT_DIR="$(cd "$git_dir_value" 2>/dev/null && pwd -P)" || exit 1
  else
    GIT_DIR="$(cd "$PROJECT_DIR/$git_dir_value" 2>/dev/null && pwd -P)" || exit 1
  fi
else
  exit 1
fi

HEAD_FILE="$GIT_DIR/HEAD"
[[ -f "$HEAD_FILE" ]] || exit 1
IFS= read -r head_value < "$HEAD_FILE" || exit 1

if [[ "$head_value" == "ref: "* ]]; then
  ref_name="${head_value#ref: }"
  COMMON_DIR="$GIT_DIR"
  if [[ -f "$GIT_DIR/commondir" ]]; then
    IFS= read -r common_dir_value < "$GIT_DIR/commondir" || exit 1
    if [[ "$common_dir_value" == /* ]]; then
      COMMON_DIR="$(cd "$common_dir_value" 2>/dev/null && pwd -P)" || exit 1
    else
      COMMON_DIR="$(cd "$GIT_DIR/$common_dir_value" 2>/dev/null && pwd -P)" || exit 1
    fi
  fi

  ref_file="$COMMON_DIR/$ref_name"
  if [[ -f "$ref_file" ]]; then
    IFS= read -r revision < "$ref_file" || exit 1
  elif [[ -f "$COMMON_DIR/packed-refs" ]]; then
    revision=""
    while IFS=' ' read -r packed_revision packed_ref; do
      if [[ "$packed_ref" == "$ref_name" ]]; then
        revision="$packed_revision"
        break
      fi
    done < "$COMMON_DIR/packed-refs"
  else
    exit 1
  fi
else
  revision="$head_value"
fi

[[ -n "${revision:-}" && ${#revision} -ge 7 && "$revision" != *[^0-9a-fA-F]* ]] || exit 1
printf "%s" "$revision"
