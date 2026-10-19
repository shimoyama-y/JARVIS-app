# JARVIS for Windows

JARVIS is a Windows desktop voice assistant. The app source is free to download. AI provider quotas and terms are set by their providers; this project cannot guarantee unlimited free AI use.

## Install

1. Download the latest `JARVIS-source.zip` from the [GitHub Releases page](https://github.com/shimoyama-y/JARVIS-app/releases).
2. Extract the ZIP to a writable folder.
3. Run `JARVIS_install.bat` and allow microphone access in Windows if prompted.
4. Run `JARVIS.bat`.

The installer sets up Python 3.12, the app's Python dependencies, Node.js if needed, and Gemini CLI. Before the first AI conversation, run `gemini` in PowerShell and sign in with your Google account. Gemini CLI is installed with `npm install -g @google/gemini-cli`; it requires Node.js 20 or newer ([official setup guide](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/index.md)). Google sets the free quota and may change it. For local fallback, install [Ollama for Windows](https://ollama.com/download/windows) and download a model supported by your PC. When neither provider is available, JARVIS's non-AI desktop features remain available.

The optional self-learning intent router uses PyTorch and is not installed by default because its download is large. Install it from `requirements-ml.txt` if desired.

## Microphone troubleshooting

Test the selected Windows input in **Settings → System → Sound → Input** and in Voice Recorder. Allow microphone access under **Settings → Privacy & security → Microphone**, including desktop apps. JARVIS uses the Windows default recording device and Google speech recognition (`ja-JP`), so speech recognition needs an internet connection.

## Developer checks

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.venv\Scripts\python.exe -m py_compile app.py ai.py features.py jarvis_tools.py
```

To publish a source bundle, push a version tag such as `v1.0.0`. GitHub Actions creates a `JARVIS-source.zip` release asset automatically.
