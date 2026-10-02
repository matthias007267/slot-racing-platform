#!/usr/bin/env bash
# Install script for Cursor Cloud/Web agents (referenced from .cursor/environment.json).
#
# Prepares a fresh Ubuntu VM so that the checks in the README work:
#   1. Qt system libraries (PySide6 needs them even for headless/offscreen runs)
#   2. uv (pinned) on the default PATH
#   3. Python dependencies from uv.lock (`uv sync --locked`)
#
# Idempotent and non-interactive. It must not start any long-running process.
set -euo pipefail

UV_VERSION="${UV_VERSION:-0.12.22}"
QT_SYSTEM_PACKAGES=(libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libglib2.0-0)

cd "$(dirname "${BASH_SOURCE[0]}")/.."

missing=()
for package in "${QT_SYSTEM_PACKAGES[@]}"; do
  dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
done
if [ "${#missing[@]}" -gt 0 ]; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
fi

# Login shells of install/start do not load ~/.bashrc, so uv must live on the default PATH.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1 || [ "$(uv --version | cut -d' ' -f2)" != "$UV_VERSION" ]; then
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_NO_MODIFY_PATH=1 sh
fi
sudo ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv
sudo ln -sf "$HOME/.local/bin/uvx" /usr/local/bin/uvx

uv --version
uv sync --locked
