import httpx
import json
import logging
import hashlib
import re

log = logging.getLogger(__name__)

OLLAMA_URL = "http://ollama.llm-system.svc.cluster.local:11434"
MODEL      = "llama3.2:3b"


def make_prompt_hash(prompt: str) -> str:
    """SHA-256 hash of prompt text — used for cache lookup."""
    return hashlib.sha256(prompt.encode()).hexdigest()


def call_ollama(prompt: str, timeout: float = 120.0) -> dict:
    """Call Ollama and return parsed JSON response."""
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1, "num_predict": 512}
            },
            timeout=timeout
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        log.debug("Raw LLM response: %s", raw[:200])

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())

        log.warning("Could not parse LLM response as JSON: %s", raw[:300])
        return {"explanation": raw[:500], "parse_error": True}

    except httpx.TimeoutException:
        log.error("Ollama request timed out after %.0fs", timeout)
        return {"error": "timeout", "explanation": "LLM request timed out"}
    except Exception as e:
        log.error("Ollama call failed: %s", e)
        return {"error": str(e), "explanation": "LLM call failed"}


def check_ollama_health() -> bool:
    """Return True if Ollama is reachable."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=10.0)
        return r.status_code == 200
    except Exception:
        return False
