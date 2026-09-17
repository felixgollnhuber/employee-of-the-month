# Direct voice path

Status as of September 17, 2026: implemented, offline tested and activated in the author's local fixed release. The direct path was also exercised in two real calls. Those calls are historical observations, not a guarantee for another installation.

## Why it changed

The original design ran a full T3 coordinator-agent turn for every meaningful utterance. That added a cold start, polling and another model round trip before the work thread could react.

The current path is:

```text
Person <-> GPT-Live 1 <-> local bridge <-> T3 work thread
                              |
                              +-> one small Responses API request for checked JSON
```

GPT-Live leads the conversation and answers from the bounded context attached before the session starts. It delegates only when T3 must be read freshly or changed.

## Structuring request

`eotm/structurer.py` sends one stateless Responses API request with JSON output, `store=false` and no conversation thread. The default model is `gpt-5.6-luna`. Set `"structuring_model": null` in `live.json` to disable this path.

The full T3 coordinator remains a fallback when the direct request fails. The same Python checks run after either path: request identity, thread identity, transcript revision, exact confirmation quote, stable command IDs and state readback.

## Call responsiveness

The service now:

- asks GPT-Live to greet only after `session.started`,
- keeps a recent status summary warm for incoming calls,
- prefetches fresh status while outgoing calls ring,
- reports structuring time separately from total backend response time,
- can play a short, quiet wait tone while backend work is running,
- waits for the model's audible goodbye before ending a caller-requested hang-up.

The wait tone can be disabled with `"wait_tone": false`. It is not counted as model output audio and does not run when the model or caller is speaking.

## Cost boundary

GPT-Live voice time and backend Responses usage are billed separately. Current model prices are linked from the main README. Exact cost depends on call duration, prompt size and the configured structuring model.
