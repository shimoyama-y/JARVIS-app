$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Get-Setting([string]$Name, [bool]$Secret) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not $value) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
    }
    if ($value) {
        return $value
    }

    if ($Secret) {
        $secureValue = Read-Host "Enter $Name (input hidden)" -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
        try {
            return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            $secureValue.Dispose()
        }
    }

    return Read-Host "Enter $Name"
}

$settings = @{
    SUPABASE_URL = Get-Setting "SUPABASE_URL" $false
    SUPABASE_ANON_KEY = Get-Setting "SUPABASE_ANON_KEY" $true
    SUPABASE_EMAIL = Get-Setting "SUPABASE_EMAIL" $false
    SUPABASE_PASSWORD = Get-Setting "SUPABASE_PASSWORD" $true
    GEMINI_API_KEY = Get-Setting "GEMINI_API_KEY" $true
}

foreach ($name in $settings.Keys) {
    if (-not $settings[$name]) {
        throw "$name is required."
    }
    [Environment]::SetEnvironmentVariable($name, $settings[$name], "Process")
}
[Environment]::SetEnvironmentVariable("JARVIS_MEMORY_BACKEND", "supabase", "Process")

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Install Python or create .venv first."
    }
    $python = $pythonCommand.Source
}

Push-Location $projectRoot
try {
    & $python "jarvis_tools.py" "memory-check"
    if ($LASTEXITCODE -ne 0) {
        throw "Connection check failed. Settings were not saved. Apply supabase\memory_tiering.sql and check the credentials, then try again."
    }
}
catch {
    foreach ($name in $settings.Keys) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    [Environment]::SetEnvironmentVariable("JARVIS_MEMORY_BACKEND", $null, "Process")
    throw
}
finally {
    Pop-Location
}

foreach ($name in $settings.Keys) {
    [Environment]::SetEnvironmentVariable($name, $settings[$name], "User")
}
[Environment]::SetEnvironmentVariable("JARVIS_MEMORY_BACKEND", "supabase", "User")

foreach ($name in $settings.Keys) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$settings.Clear()
[Environment]::SetEnvironmentVariable("JARVIS_MEMORY_BACKEND", $null, "Process")
Write-Host "Memory services are configured for this Windows user. Restart JARVIS before use."
