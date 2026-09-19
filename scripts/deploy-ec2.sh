#!/usr/bin/env bash
set -euo pipefail

repo=/home/ubuntu/sgcc-wiki-backend
prep="$repo/.uv-prep"
uv="$prep/bin/uv"
revision="$(git -C "$repo" rev-parse HEAD)"
environment="$prep/releases/$revision/venv"
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

mkdir -p "$prep/bin" "$prep/cache" "$prep/releases/$revision"
if [ ! -x "$uv" ]; then
  curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/0.12.17/install.sh |
    env UV_UNMANAGED_INSTALL="$prep/bin" sh
fi
"$uv" --version

cd "$repo"
UV_CACHE_DIR="$prep/cache" \
UV_PROJECT_ENVIRONMENT="$environment" \
UV_PYTHON_DOWNLOADS=never \
  "$uv" sync --locked --no-dev --no-editable --python "$repo/.venv/bin/python"
test -x "$environment/bin/uvicorn"

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
    trap - ERR
    echo "Backend deployment healthy at $revision"
    exit 0
  fi
  sleep 1
done

false
