import requests

def generate(base_url, model, prompt):
    url = f"{base_url.rstrip('/')}/api/generate"

    print("=" * 60, flush=True)
    print("OLLAMA URL :", repr(url), flush=True)
    print("MODEL      :", repr(model), flush=True)
    print("PROMPT     :", repr(prompt[:100]), flush=True)
    print("=" * 60, flush=True)

    r = requests.post(
        url,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False
        },
        timeout=600
    )

    print("STATUS :", r.status_code, flush=True)
    print("BODY   :", r.text[:500], flush=True)

    r.raise_for_status()

    return r.json()["response"]
