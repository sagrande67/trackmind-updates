@echo off
REM push.bat - Aggiorna repo GitHub trackmind-updates con la versione
REM letta da version.py presente in questa stessa cartella.
REM
REM Uso: doppio click su push.bat (oppure .\push.bat da cmd)

setlocal enabledelayedexpansion

REM Spostati nella cartella dello script
cd /d "%~dp0"

REM Verifica che version.py esista
if not exist "version.py" (
    echo ERRORE: version.py non trovato in %CD%
    echo Hai lanciato PREPARA GITHUB dall'app prima di eseguire questo script?
    pause
    exit /b 1
)

REM Estrai versione: cerca la riga __version__ = '05.05.07'
REM Usa ' come delimitatore e prende il 2o token (tra i due apici)
set VER=
for /f "tokens=2 delims='" %%a in ('findstr /r "__version__" version.py') do (
    set VER=%%a
)

if "%VER%"=="" (
    echo ERRORE: impossibile leggere __version__ da version.py
    pause
    exit /b 1
)

echo.
echo =====================================================
echo  TrackMind GitHub push - versione %VER%
echo =====================================================
echo.

REM Verifica identita' git (se manca il commit fallisce silenziosamente)
for /f "delims=" %%u in ('git config user.email 2^>nul') do set GITMAIL=%%u
if "%GITMAIL%"=="" (
    echo ERRORE: git user.email non configurato.
    echo Esegui una volta sola:
    echo    git config --global user.email "tuo@email.it"
    echo    git config --global user.name  "Tuo Nome"
    pause
    exit /b 1
)

echo ^>^> git add .
git add .

echo ^>^> git commit -m "v%VER%"
git commit -m "v%VER%"
set COMMIT_EC=%errorlevel%
REM errorlevel 1 puo' significare:
REM   - niente da committare (ok, continuiamo col push)
REM   - commit fallito (identita', hook, ecc.) - mostriamo un avviso ma
REM     il push successivo diagnosticera' meglio se manca qualcosa.
if not "%COMMIT_EC%"=="0" (
    echo.
    echo [Avviso] git commit ha restituito codice %COMMIT_EC%.
    echo          Se era solo "niente da committare" ignora. Altrimenti
    echo          controlla il messaggio sopra.
    echo.
)

echo ^>^> git push
git push
if errorlevel 1 (
    echo.
    echo ERRORE durante il push - controlla credenziali o connessione
    pause
    exit /b 1
)

echo.
echo =====================================================
echo  OK - v%VER% pubblicata su GitHub
echo  L'uConsole ora puo' aggiornarsi via Wi-Fi
echo =====================================================
echo.
pause
