# push.ps1 - Aggiorna repo GitHub trackmind-updates con la versione
# letta da version.py presente nella cartella corrente.
#
# Uso:
#   - Doppio click sul file (se PowerShell e' configurato per esecuzione)
#   - Oppure da terminale:  .\push.ps1
#
# Flusso:
#   1. Legge __version__ da version.py
#   2. git add .
#   3. git commit -m "vXX.XX.XX"
#   4. git push
#
# Se non ci sono modifiche (o il commit fallisce), continua lo stesso col push.

$ErrorActionPreference = "Continue"

# Spostati nella cartella dello script (cosi' funziona anche da doppio click)
Set-Location -Path $PSScriptRoot

# Verifica che version.py esista
if (-not (Test-Path ".\version.py")) {
    Write-Host "ERRORE: version.py non trovato in $PSScriptRoot" -ForegroundColor Red
    Write-Host "Hai lanciato 'PREPARA GITHUB' dall'app prima di eseguire questo script?"
    Read-Host "Premi INVIO per chiudere"
    exit 1
}

# Estrai versione da version.py (riga: __version__ = '05.05.07')
$match = Select-String -Path ".\version.py" -Pattern "__version__\s*=\s*'([^']+)'"
if (-not $match) {
    Write-Host "ERRORE: impossibile leggere __version__ da version.py" -ForegroundColor Red
    Read-Host "Premi INVIO per chiudere"
    exit 1
}
$ver = $match.Matches.Groups[1].Value
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host " TrackMind GitHub push - versione $ver" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""

# Stage tutto
Write-Host ">> git add ." -ForegroundColor Cyan
git add .

# Commit (ignora errore se niente e' cambiato)
Write-Host ">> git commit -m `"v$ver`"" -ForegroundColor Cyan
git commit -m "v$ver"
if ($LASTEXITCODE -ne 0) {
    Write-Host "(nessuna modifica da committare, procedo col push)" -ForegroundColor Yellow
}

# Push
Write-Host ">> git push" -ForegroundColor Cyan
git push
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERRORE durante il push - controlla credenziali o connessione" -ForegroundColor Red
    Read-Host "Premi INVIO per chiudere"
    exit 1
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host " OK - v$ver pubblicata su GitHub" -ForegroundColor Green
Write-Host " L'uConsole ora puo' aggiornarsi via Wi-Fi" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Read-Host "Premi INVIO per chiudere"
