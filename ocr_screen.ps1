# 画像ファイルから文字を読み取る (Windows標準のOCR機能。追加のインストール不要・ネットに送らない)
# 使い方: powershell -File ocr_screen.ps1 -ImagePath 画像のパス -OutputPath 結果を書き出すテキストファイル
#        -WordsPath を指定すると、単語ごとの画像内の座標 (x, y, w, h) もJSONで書き出す (画面操作の自動化で使う)
# 終了コード: 0=成功 (文字なしもここに含む) / 2=画像を読み込めない / 3=OCR言語が使えない / 4=認識に失敗
param(
    [Parameter(Mandatory=$true)][string]$ImagePath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$WordsPath
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] | Out-Null

try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
} catch {
    [Console]::Error.WriteLine("SCREENSHOT_READ_ERROR: $_")
    exit 2
}

# OcrEngineが受け付けるのはBgra8+事前乗算アルファの形式だけ。変換しないと例外になり常に失敗する
if ($bitmap.BitmapPixelFormat -ne [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8 -or
    $bitmap.BitmapAlphaMode -eq [Windows.Graphics.Imaging.BitmapAlphaMode]::Straight) {
    $bitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bitmap,
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
    [Console]::Error.WriteLine("NO_OCR_LANGUAGE: OCR language pack is not installed")
    exit 3
}

try {
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
} catch {
    [Console]::Error.WriteLine("OCR_FAILED: $_")
    exit 4
}
[IO.File]::WriteAllText($OutputPath, $result.Text, [Text.Encoding]::UTF8)

if ($WordsPath) {
    # 要素が1つのときにJSON配列ではなくなる(PowerShellのConvertTo-Jsonの仕様)問題を避けるため、文字列を手で組み立てる
    $items = foreach ($line in $result.Lines) {
        foreach ($word in $line.Words) {
            $rect = $word.BoundingRect
            [PSCustomObject]@{
                text = $word.Text
                x = [math]::Round($rect.X)
                y = [math]::Round($rect.Y)
                w = [math]::Round($rect.Width)
                h = [math]::Round($rect.Height)
            } | ConvertTo-Json -Compress
        }
    }
    [IO.File]::WriteAllText($WordsPath, "[" + ($items -join ",") + "]", [Text.Encoding]::UTF8)
}
