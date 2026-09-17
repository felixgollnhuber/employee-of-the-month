# Project rules for coding agents

- Write code, comments, docs and commit messages in English.
- The voice path is GPT-Live 1 over the OpenAI API, with Telegram audio and answers returned to the originating T3 Code thread. Do not silently switch to another realtime or text-to-speech stack.
- Builds and offline checks must never affect a running call or the installed service.
- Audio capture, playback, route changes, logins and real calls are explicit runtime actions. `make check` must work without any of them.
- Never commit credentials, recordings, transcripts, phone numbers, installers, app bundles or copies of proprietary source code.
- Keep offline test results, real-service measurements and user confirmation clearly apart in docs and PR descriptions.
- User-facing phrases and language heuristics live in `eotm/i18n.py`. Add or change them for every supported language, with tests.
- Read `docs/architecture.md` before changing the call, conversation or T3 delegation flow.
