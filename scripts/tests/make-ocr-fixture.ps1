# B32: renders the OCR oracle fixture services/api/tests/fixtures/documents/metin.png
# (white 600x120, Arial 28, "Merhaba Dünya 1234"). Re-run only to regenerate; truth.json
# then needs the new size/sha256 (scripts/tests/make-document-fixtures.py records them).
param([string]$Out = (Join-Path $PSScriptRoot "..\..\services\api\tests\fixtures\documents\metin.png"))
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap 600,120
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$font = New-Object System.Drawing.Font("Arial", 28)
$g.DrawString("Merhaba Dünya 1234", $font, [System.Drawing.Brushes]::Black, 10, 30)
$g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Host "wrote $Out"
