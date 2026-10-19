@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$processes = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('pythonw.exe','python.exe') -and $_.CommandLine -match 'app\.py' }; if ($processes) { $processes | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host 'JARVIS stopped.' } else { Write-Host 'JARVIS process was not found.' }"
timeout /t 3 /nobreak >nul
