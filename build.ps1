param(
    [string]$Python = (Join-Path $PSScriptRoot '../.venv/Scripts/python.exe'),
    [string]$Destination = '发布'
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -X utf8 tools/collect_notices.py
    if ($LASTEXITCODE -ne 0) { throw 'Could not collect third-party notices.' }
    & $Python -X utf8 tools/make_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate application icon.' }
    if (!(Test-Path -LiteralPath 'licenses/GPL-3.0.txt')) { throw 'GPL-3.0.txt must be included.' }
    $buildArguments = @('-X', 'utf8', '-m', 'PyInstaller', '--noconfirm',
                        '--distpath', $Destination, '--workpath', 'build', 'Infrared.spec')
    & $Python @buildArguments
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
    $release = Join-Path (Join-Path $PSScriptRoot $Destination) '红外预处理'
    Copy-Item -LiteralPath 'README.md' -Destination (Join-Path $release '使用说明.md')
    Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.md' -Destination $release
    Copy-Item -LiteralPath 'licenses' -Destination $release -Recurse -Force
    Write-Host "Built: $release"
} finally {
    Pop-Location
}
