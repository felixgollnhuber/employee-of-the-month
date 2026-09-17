"""One stateless model request that turns a phone transcript into checked JSON.

Replaces the per-call T3 coordination thread in the conversation path. No thread,
no provider session and no polling; every safety check stays in the caller."""
import json
import re
import urllib.error
import urllib.request

from .control import GateError

RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6-luna"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise GateError("structurer_redirect_rejected")


class Structurer:
    def __init__(self, api_key, *, model=DEFAULT_MODEL, opener=None, timeout=12):
        if not isinstance(api_key, str) or not api_key.startswith("sk-"):
            raise GateError("openai_api_key_required")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model):
            raise GateError("invalid_structuring_model")
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    @classmethod
    def from_config(cls, config):
        """live.json: structuring_model names the model; an explicit null keeps the T3 coordinator."""
        if "structuring_model" in config and config["structuring_model"] is None: return None
        return cls(config.get("api_key"), model=config.get("structuring_model", DEFAULT_MODEL))

    def __call__(self, prompt):
        if not isinstance(prompt, str) or not 0 < len(prompt) <= 200000:
            raise GateError("invalid_structurer_prompt")
        body = {"model": self.model, "input": prompt, "store": False, "reasoning": {"effort": "none"},
                "text": {"format": {"type": "json_object"}}, "max_output_tokens": 2000}
        request = urllib.request.Request(RESPONSES_URL, data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read(1024*1024 + 1)
            if len(data) > 1024*1024: raise GateError("structurer_response_too_large")
            result = json.loads(data)
        except urllib.error.HTTPError as error:
            # Never surface the response body, headers or key.
            raise GateError("structurer_http_" + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise GateError("structurer_connection_or_response_failed") from None
        output = result.get("output") if isinstance(result, dict) else None
        parts = [part.get("text") for item in output or [] if isinstance(item, dict) and item.get("type") == "message"
                 for part in item.get("content") or [] if isinstance(part, dict) and part.get("type") == "output_text"]
        text = "".join(part for part in parts if isinstance(part, str))
        if not text: raise GateError("structurer_response_invalid")
        return text


def structurer_for_profile(profile):
    from .config import read_live_config
    return Structurer.from_config(read_live_config(profile))
