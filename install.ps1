$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($env:OS -ne "Windows_NT") {
    throw "JARVIS desktop UI currently requires Windows 10 or 11."
}

function Find-Python312 {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) { return }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python -c "import sys; print(sys.executable if sys.version_info[:2] == (3, 12) else '')" 2>$null
        if ($LASTEXITCODE -eq 0) { return }
    }
    return $null
}

$python = Find-Python312
if (-not $python) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 is required. Install it from https://www.python.org/downloads/ and rerun this installer."
    }
    Write-Host "Installing Python 3.12..." -ForegroundColor Cyan
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Python installation failed. Install Python 3.12 manually, then rerun." }
    $python = Find-Python312
}
if (-not $python) { throw "Python 3.12 was not found. Restart PowerShell after installing it, then rerun." }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the JARVIS virtual environment." }
}
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "Installing JARVIS dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check the network and rerun this installer." }
& $venvPython -m py_compile app.py ai.py features.py jarvis_tools.py
if ($LASTEXITCODE -ne 0) { throw "JARVIS source validation failed." }

Write-Host "Checking the free-first Gemini CLI..." -ForegroundColor Cyan
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Write-Warning "Node.js installation failed; install it later from https://nodejs.org/" }
        $env:Path += ";$env:ProgramFiles\nodejs;$env:APPDATA\npm"
    } else {
        Write-Warning "Node.js is missing. Install Node.js 20+ from https://nodejs.org/ to use Gemini CLI."
    }
}
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm install --global @google/gemini-cli
    if ($LASTEXITCODE -ne 0) { Write-Warning "Gemini CLI installation failed; rerun: npm install -g @google/gemini-cli" }
    $env:Path += ";$env:APPDATA\npm"
} else {
    Write-Warning "npm is unavailable; install Node.js 20+ and Gemini CLI later."
}

Write-Host "Setup complete. Start JARVIS with JARVIS.bat." -ForegroundColor Green
Write-Host "Before first use, run 'gemini' in PowerShell and sign in with your Google account."
Write-Host "Gemini free usage has provider limits; install Ollama separately for local fallback."
Write-Host "The optional self-learning router can be installed with: .venv\Scripts\python.exe -m pip install -r requirements-ml.txt"
Read-Host "Press Enter to close"
