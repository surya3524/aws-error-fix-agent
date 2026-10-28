#!/usr/bin/env bash
# Builds agent.zip for AgentCore Runtime direct code deployment (Linux arm64, Python 3.13).
# Run in AWS CloudShell (or any Linux/macOS shell) from this folder.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --user --quiet uv
export PATH="$HOME/.local/bin:$PATH"

rm -rf deployment_package agent.zip
uv pip install \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.13 \
  --target=deployment_package \
  --only-binary=:all: \
  bedrock-agentcore boto3

cp -r main.py sample_app deployment_package/
find deployment_package -type d -exec chmod 755 {} +
find deployment_package -type f -exec chmod 644 {} +
(cd deployment_package && zip -qr ../agent.zip .)
echo "Built $(pwd)/agent.zip ($(du -h agent.zip | cut -f1))"
