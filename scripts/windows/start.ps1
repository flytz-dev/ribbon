<#
Start the RIBBON server and open it in the browser. Run it through start-server.bat.

-Lan        also accept other computers on the same network (for the multi-user demo).
-Port       first port to try; the next free one is used when it is taken.
-NoBrowser  do not open the browser.
-Demo       use the demonstration database (pyconfer-demo.db), kept apart from real audits;
            it is prepared with tools.seed_demo the first time. Run it through start-demo.bat.
#>

param(
    [switch]$Lan,
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$Demo
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $ProjectRoot

if (-not (Test-Path $VenvPython)) {
    Write-Fail "A instalação ainda não foi feita neste PC. Dê dois cliques em setup.bat primeiro."
    exit 1
}

$tesseract = Find-Tesseract
$poppler = Find-Poppler
if (-not $tesseract) { Write-Warn "Tesseract não encontrado: o servidor abre, mas as auditorias vão falhar. Rode o setup.bat." }
if (-not $poppler) { Write-Warn "Poppler não encontrado: o servidor abre, mas as auditorias vão falhar. Rode o setup.bat." }
Set-ToolEnvironment $tesseract $poppler

if ($Demo) {
    $env:RIBBON_DB = Join-Path $ProjectRoot "pyconfer-demo.db"
    if (-not (Test-Path $env:RIBBON_DB)) {
        Write-Host "Preparando a base de demonstração (auditorias reais de lotes sintéticos, cerca de 2 minutos)..." -ForegroundColor Yellow
        & $VenvPython -m tools.seed_demo
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Não foi possível preparar a base de demonstração."
            exit 1
        }
    }
    Write-Host "Modo demonstração: base $($env:RIBBON_DB)" -ForegroundColor Yellow
    Write-Host "Conta: demonstracao  ·  senha: ribbon-demo-2026  (somente para demonstração)" -ForegroundColor Yellow
}

while (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { $Port++ }

$bindAddress = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
$url = "http://127.0.0.1:$Port"

Write-Host ""
Write-Host "R.I.B.B.O.N." -ForegroundColor White
Write-Host "Abrindo em: $url" -ForegroundColor Green
if ($Lan) {
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress
    foreach ($address in $addresses) { Write-Host "Outros computadores da rede: http://${address}:$Port" -ForegroundColor Green }
    Write-Host "Se o Windows perguntar sobre o firewall, permita o acesso em redes privadas."
}
Write-Host ""
Write-Host "Para parar o servidor: Ctrl+C nesta janela."
Write-Host "Comando equivalente, no PowerShell dentro da pasta do projeto:"
Write-Host "    `$env:TESSERACT_CMD = `"$tesseract`""
Write-Host "    `$env:POPPLER_PATH = `"$poppler`""
Write-Host "    .\.venv\Scripts\python.exe -m uvicorn api.main:app --host $bindAddress --port $Port"
Write-Host ""

# Open the browser once the server has had a moment to start.
if (-not $NoBrowser) {
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", "Start-Sleep -Seconds 3; Start-Process '$url'"
}

& $VenvPython -m uvicorn api.main:app --host $bindAddress --port $Port
