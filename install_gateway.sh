#!/bin/bash
set -e

BASE=/opt/gravityzone/bot
GW=$BASE/gateway

echo "== Gravity Gateway Installer =="

cd "$BASE"

source venv/bin/activate

pip install -U fastapi uvicorn httpx python-dotenv

mkdir -p "$GW"

cat > "$GW/models.py" <<'PY'
MODELS = {
    "gpt": {
        "provider": "openrouter",
        "model": "openai/gpt-4.1-mini",
        "name": "GPT"
    },
    "gravity-ai": {
        "provider": "node1",
        "model": "partai/dorna-llama3:8b-instruct-q4_0",
        "name": "Gravity AI"
    },
    "phi": {
        "provider": "node1",
        "model": "phi3.5:latest",
        "name": "Phi"
    },
    "qwen": {
        "provider": "node2",
        "model": "qwen3:8b",
        "name": "Qwen3"
    },
    "gemma": {
        "provider": "node2",
        "model": "gemma3:12b",
        "name": "Gemma3"
    },
    "deepseek": {
        "provider": "node2",
        "model": "deepseek-r1:8b",
        "name": "DeepSeek R1"
    }
}
PY

cat > "$GW/providers.py" <<'PY'
NODES = {
    "node1": {
        "url": "https://ol.gravityzoneshop.top"
    },
    "node2": {
        "url": "https://oll.gravityzoneshop.top"
    }
}
PY

touch "$GW/router.py"
touch "$GW/app.py"

echo
echo "=================================="
echo " Gravity Gateway Part 1 Installed "
echo "=================================="

echo
ls -lah "$GW"
