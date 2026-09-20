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

rollback() {
  trap - ERR
  if [ "$switched" -eq 1 ]; then
    if [ "$had_override" -eq 1 ]; then
      sudo cp "$previous" "$override"
    else
      sudo rm -f "$override"
    fi
    sudo systemctl daemon-reload
    sudo systemctl restart sgcc-wiki
    sudo systemctl is-active --quiet sgcc-wiki
    curl --silent --show-error --fail --max-time 5 http://127.0.0.1:8000/healthz >/dev/null
  fi
  echo 'Backend deployment failed; previous service configuration restored.' >&2
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
test -x "$environment/bin/uvicorn"

# Validate the installed wheel before switching the service.
installed_documents="$("$environment/bin/python" -c 'import sgcc_wiki_backend.routers.documents as documents; print(documents.__file__)')"
cmp "$repo/src/sgcc_wiki_backend/routers/documents.py" "$installed_documents"

if sudo test -f "$override"; then
  sudo cat "$override" > "$previous"
  had_override=1
fi
sudo mkdir -p /etc/systemd/system/sgcc-wiki.service.d
switched=1
printf '[Service]\nExecStart=\nExecStart=%s/bin/uvicorn sgcc_wiki_backend:app --host 127.0.0.1 --port 8000\n' "$environment" |
  sudo tee "$override" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart sgcc-wiki
sudo systemctl is-active --quiet sgcc-wiki

for attempt in {1..10}; do
  if curl --silent --show-error --fail --max-time 5 http://127.0.0.1:8000/healthz >/dev/null; then
    if ! "$environment/bin/python" -c 'import json, urllib.request; paths = json.load(urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=5))["paths"]; assert "/documents/by-title/likes" in paths'; then
      break
    fi
    trap - ERR
    echo "Backend deployment healthy at $revision"
    exit 0
  fi
  sleep 1
done

false
