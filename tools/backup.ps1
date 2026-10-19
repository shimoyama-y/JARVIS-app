# JARVIS のバックアップ
#   Google ドライブへ (毎日):  powershell -ExecutionPolicy Bypass -File tools\backup.ps1 -Target drive
#   外付け HDD へ (月1回):     powershell -ExecutionPolicy Bypass -File tools\backup.ps1 -Target hdd
#
# drive: Google ドライブ パソコン版の「マイドライブ\JARVIS_backup」へ JARVIS フォルダを丸ごと複製する
#        (片方向の複製なので .git を Drive で直接同期するより安全)
# hdd:   ボリューム名が JARVIS_BACKUP の外付けディスクに、月ごとのフォルダ (例: JARVIS_2026-10) で保存する
#        (取り違えを防ぐため、そのボリューム名のディスクにだけ書き込む。古いものは 6 か月分残す)
param(
    [ValidateSet("drive", "hdd")] [string]$Target = "drive",
    [string]$HddLabel = "JARVIS_BACKUP",
    [int]$KeepMonths = 6
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot          # JARVIS フォルダ
$LogFile = Join-Path $Source "backups\backup.log"
New-Item -ItemType Directory -Force (Split-Path $LogFile) | Out-Null

function Write-Log($msg) {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Target, $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Output $line
}

function Find-GoogleDrive {
    # Google ドライブ パソコン版はドライブ文字 (多くは G:) にマイドライブを表示する
    foreach ($d in Get-PSDrive -PSProvider FileSystem) {
        foreach ($name in @("マイドライブ", "My Drive")) {
            $p = Join-Path $d.Root $name
            if (Test-Path $p) { return $p }
        }
    }
    return $null
}

function Copy-Jarvis($dest) {
    New-Item -ItemType Directory -Force $dest | Out-Null
    # /MIR: 複製 (消したファイルは複製先からも消す)  /XD: 一時フォルダは除く  /R /W: 失敗時の再試行を短く
    robocopy $Source $dest /MIR /XD __pycache__ /XF "*.pyc" /R:2 /W:5 /NP /NFL /NDL /NJH | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy が失敗しました (終了コード $LASTEXITCODE)" }
}

try {
    if ($Target -eq "drive") {
        $drive = Find-GoogleDrive
        if (-not $drive) { throw "Google ドライブ (マイドライブ) が見つかりません。Google ドライブ パソコン版が起動しているか確認してください" }
        $dest = Join-Path $drive "JARVIS_backup"
        Copy-Jarvis $dest
        Write-Log "Google ドライブへ複製しました: $dest"
    }
    else {
        $vol = Get-Volume | Where-Object { $_.FileSystemLabel -eq $HddLabel -and $_.DriveLetter } | Select-Object -First 1
        if (-not $vol) { throw "ボリューム名が $HddLabel の外付けディスクが見つかりません。つないでから、もう一度実行してください" }
        $root = "{0}:\JARVIS_backups" -f $vol.DriveLetter
        $dest = Join-Path $root ("JARVIS_" + (Get-Date -Format "yyyy-MM"))
        Copy-Jarvis $dest
        # 古い月のバックアップを整理 (新しいものから $KeepMonths 個残す)
        Get-ChildItem $root -Directory -Filter "JARVIS_*" | Sort-Object Name -Descending |
            Select-Object -Skip $KeepMonths | ForEach-Object { Remove-Item $_.FullName -Recurse -Force; Write-Log "古いバックアップを削除: $($_.Name)" }
        Write-Log "外付け HDD へ保存しました: $dest"
    }
    exit 0
}
catch {
    Write-Log "失敗: $($_.Exception.Message)"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("JARVIS のバックアップに失敗しました。`n$($_.Exception.Message)", "JARVIS バックアップ") | Out-Null
    exit 1
}
