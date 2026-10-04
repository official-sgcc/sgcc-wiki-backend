#!/usr/bin/env bash
set -euo pipefail

repo=/home/ubuntu/sgcc-wiki-backend
prep="$repo/.uv-prep"
uv="$prep/bin/uv"
revision="$(git -C "$repo" rev-parse HEAD)"
release_dir="$prep/releases/$revision"
environment="$release_dir/venv"
override=/etc/systemd/system/sgcc-wiki.service.d/uv-runtime.conf
previous="$prep/previous-uv-runtime.conf"
had_override=0
switched=0
health_timeout=60

wait_for_health() {
  local deadline=$((SECONDS + health_timeout))

  while (( SECONDS < deadline )); do
    if sudo systemctl is-active --quiet sgcc-wiki \
      && curl --silent --show-error --fail \
        --connect-timeout 1 --max-time 3 \
        http://127.0.0.1:8000/healthz >/dev/null; then
      return 0
    fi
    sleep 1
  done

  return 1
}

service_uses_environment() {
  sudo systemctl show sgcc-wiki --property=ExecStart --value |
    grep --fixed-strings --quiet -- "$environment/bin/uvicorn"
}

openapi_is_healthy() {
  "$environment/bin/python" -c '
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=3) as response:
    schema = json.load(response)

assert isinstance(schema.get("paths"), dict)
assert "/healthz" in schema["paths"]
'
}

wait_for_release() {
  local deadline=$((SECONDS + health_timeout))

  while (( SECONDS < deadline )); do
    if sudo systemctl is-active --quiet sgcc-wiki \
      && service_uses_environment \
      && curl --silent --show-error --fail \
        --connect-timeout 1 --max-time 3 \
        http://127.0.0.1:8000/healthz >/dev/null \
      && openapi_is_healthy; then
      return 0
    fi
    sleep 1
  done

  return 1
}

rollback() {
  local rollback_ok=1

  trap - ERR
  set +e
  if [ "$switched" -eq 1 ]; then
    if [ "$had_override" -eq 1 ]; then
      sudo cp "$previous" "$override" || rollback_ok=0
    else
      sudo rm -f "$override" || rollback_ok=0
    fi
    sudo systemctl daemon-reload || rollback_ok=0
    sudo systemctl restart sgcc-wiki || rollback_ok=0
    wait_for_health || rollback_ok=0

    if [ "$rollback_ok" -eq 1 ]; then
      echo 'Backend deployment failed; previous service configuration restored.' >&2
    else
      echo 'Backend deployment failed and the previous service did not recover.' >&2
    fi
  else
    echo 'Backend deployment failed before the service switch; the active service was not changed.' >&2
  fi
  exit 1
}
trap rollback ERR

mkdir -p "$prep/bin" "$prep/cache" "$release_dir/dist"
if [ ! -x "$uv" ]; then
  curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/0.12.17/install.sh |
    env UV_UNMANAGED_INSTALL="$prep/bin" sh
fi
"$uv" --version

cd "$repo"
UV_CACHE_DIR="$prep/cache" \
UV_PROJECT_ENVIRONMENT="$environment" \
UV_PYTHON_DOWNLOADS=never \
  "$uv" sync --locked --no-dev --no-install-project --python "$repo/.venv/bin/python"
UV_CACHE_DIR="$prep/cache" UV_PYTHON_DOWNLOADS=never \
  "$uv" build --wheel --out-dir "$release_dir/dist" --python "$repo/.venv/bin/python"
wheels=("$release_dir"/dist/*.whl)
test "${#wheels[@]}" -eq 1
UV_CACHE_DIR="$prep/cache" UV_PYTHON_DOWNLOADS=never \
  "$uv" pip install --python "$environment/bin/python" --no-deps "${wheels[0]}"
UV_CACHE_DIR="$prep/cache" UV_PYTHON_DOWNLOADS=never \
  "$uv" pip check --python "$environment/bin/python"
test -x "$environment/bin/uvicorn"

EXPECTED_ENVIRONMENT="$environment" "$environment/bin/python" -c '
import importlib.util
import os
from pathlib import Path

environment = Path(os.environ["EXPECTED_ENVIRONMENT"]).resolve()
spec = importlib.util.find_spec("sgcc_wiki_backend")
assert spec is not None and spec.submodule_search_locations
package_path = Path(next(iter(spec.submodule_search_locations))).resolve()
assert package_path.is_relative_to(environment)
'

if sudo test -f "$override"; then
  sudo cat "$override" > "$previous"
  had_override=1
fi
sudo mkdir -p /etc/systemd/system/sgcc-wiki.service.d
switched=1
printf '[Service]\nWorkingDirectory=%s\nExecStart=\nExecStart=%s/bin/uvicorn sgcc_wiki_backend:app --host 127.0.0.1 --port 8000\n' "$repo" "$environment" |
  sudo tee "$override" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart sgcc-wiki

if ! wait_for_release; then
  echo "Backend release $revision did not become healthy within ${health_timeout}s." >&2
  false
fi

trap - ERR
echo "Backend deployment healthy at $revision"
