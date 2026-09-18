#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
action=up
demo=()
import_args=()
bootstrap_args=()
no_build=false
while (($#)); do
  case "$1" in
    up|stop|status|logs) action="$1"; shift ;;
    --demo) demo=(--demo); shift ;;
    --no-build) no_build=true; shift ;;
    --import-env)
      import_file="$(cd -- "$(dirname -- "$2")" && pwd)/$(basename -- "$2")"
      import_args=(--mount "type=bind,source=$import_file,target=/import.env,readonly")
      bootstrap_args=(--import-env /import.env)
      shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
docker info --format '{{.OSType}}'
config_directory="$PWD/.data/local"
compose=(docker compose --project-name sec-filing-local --env-file "$config_directory/runtime.env" -f infra/local/compose.yaml)
if [[ "$action" == up ]]; then
  mkdir -p -- "$config_directory"
  if [[ "$no_build" == false ]]; then
    docker build -f apps/backend/Dockerfile -t sec-filing-agent-backend:local .
  fi
  docker run --rm --pull never --user "$(id -u):$(id -g)" --mount "type=bind,source=$config_directory,target=/config" \
    "${import_args[@]}" --entrypoint python sec-filing-agent-backend:local infra/local/bootstrap.py \
    "${bootstrap_args[@]}" "${demo[@]}"
  "${compose[@]}" config --quiet
  if [[ "$no_build" == false ]]; then "${compose[@]}" build web; fi
  "${compose[@]}" up -d --no-build --wait --wait-timeout 300
  echo 'Ready: https://localhost:8443 (local self-signed certificate).'
  echo 'Model/SEC settings: .data/local/provider.env; re-run after editing.'
else
  case "$action" in
    stop) "${compose[@]}" stop ;;
    status) "${compose[@]}" ps -a ;;
    logs) "${compose[@]}" logs --tail 100 api worker dispatcher reconciler beat web ;;
  esac
fi
