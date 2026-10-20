# Galam AI CLI

Galam is a terminal AI assistant with chat, file inspection and editing, shell actions, action review, and support for multiple model providers and local OpenAI-compatible servers.

## Run

Python 3.10 or newer is recommended. The shell workflow targets Linux and Android/Termux.

```sh
python -m pip install -r requirements.txt
python galam.py
```

Optional LangGraph support has a separate dependency file, `requirements-lang.txt`.

## API credentials

Use provider environment variables such as `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `GROQ_API_KEY`, or `CLOUDFLARE_API_TOKEN`. Alternatively, copy `storage/keys.example.json` to `storage/keys.json` and fill in credentials locally. The real keys file is ignored by Git.

Provider URLs, model profiles, API request code, and environment variable lookups are included. API keys, saved personal settings, conversations, and runtime logs are excluded.

## Local model server

Select **Local Endpoint** in Galam's settings. The default base URL is `http://127.0.0.1:8080/v1`; model IDs are discovered from the server. Configure another address in settings or through `LOCAL_ENDPOINT_URL` when no saved address is set. No model weights are bundled.

For automatic llama.cpp startup, set `GALAM_LLAMA_SERVER_BIN` and `GALAM_LOCAL_MODEL_PATH` to your executable and GGUF file. Otherwise, start your compatible server yourself.

## Actions and edits

The existing review modes control tool approval. File changes display numbered red deletions and green additions. Python/JSON writes receive built-in syntax checks; optional project checks can be configured in `.galam-checks.json`. Exact action output and edit recovery records are retained locally.

## Tests

```sh
python -m unittest discover -s tests -p 'test_pipelines.py'
```

Pipeline design and verification notes are in `artifacts/PIPELINE_UPGRADE_NOTES.md`.
