<#
  Clipmaschine - Installation auf den Desktop und Start (Windows 10/11)

  In PowerShell einfuegen und Enter druecken:
    irm https://raw.githubusercontent.com/mpoffizial/Claude/claude/blissful-turing-83j3hp/clipmaschine/deploy/install_windows.ps1 | iex

  Was passiert:
    1. Fehlende Programme (Python, ffmpeg) werden per winget installiert.
    2. Die Clipmaschine kommt nach Desktop\Clipmaschine. Liegt der Desktop in OneDrive, wird
       stattdessen lokal installiert (Sync-Konflikte vermeiden) und auf dem Desktop liegen
       Verknuepfungen. Bei Updates bleiben config.yaml, .env, Logins und Daten erhalten.
    3. Auf dem Desktop entstehen "Clipmaschine starten" und "Clipmaschine aktualisieren".
    4. Die Maschine startet - beim ersten Mal mit Einrichtungs-Assistent und TikTok-Anmeldung.

  Erneut ausfuehren (oder "Clipmaschine aktualisieren") = auf die neueste Version bringen.
#>
& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'      # macht Downloads in Windows PowerShell deutlich schneller
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

    $Repo    = 'mpoffizial/Claude'
    $Branch  = 'claude/blissful-turing-83j3hp'
    $ZipUrl  = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
    $SelfUrl = "https://raw.githubusercontent.com/$Repo/$Branch/clipmaschine/deploy/install_windows.ps1"
    $OnWindows = ($env:OS -eq 'Windows_NT')

    if ($env:CLIPMASCHINE_DESKTOP) {
        $Desktop = $env:CLIPMASCHINE_DESKTOP
    } else {
        $Desktop = [Environment]::GetFolderPath('Desktop')
    }
    $InstallBase = $Desktop
    $ViaOneDrive = $false
    if ($OnWindows -and ($Desktop -match 'OneDrive') -and $env:LOCALAPPDATA) {
        $InstallBase = $env:LOCALAPPDATA
        $ViaOneDrive = $true
    }
    $Target = Join-Path $InstallBase 'Clipmaschine'

    function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
    function Write-Ok([string]$Text) { Write-Host "    OK  $Text" -ForegroundColor Green }

    function Update-SessionPath {
        $parts = @([Environment]::GetEnvironmentVariable('Path', 'Machine'),
                   [Environment]::GetEnvironmentVariable('Path', 'User')) | Where-Object { $_ }
        $env:Path = $parts -join ';'
    }

    function Test-Python {
        # "python" kann der Microsoft-Store-Platzhalter sein - deshalb den py-Launcher pruefen
        if (-not (Get-Command py -ErrorAction SilentlyContinue)) { return $false }
        & py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    }

    function Install-WithWinget([string]$Id, [string]$Label, [string[]]$Extra) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw "$Label fehlt und winget ist nicht verfuegbar. Bitte $Label von Hand installieren und den Befehl erneut ausfuehren."
        }
        Write-Step "$Label wird installiert (winget) ..."
        $wingetArgs = @('install', '--id', $Id, '-e', '--accept-package-agreements', '--accept-source-agreements') + $Extra
        & winget @wingetArgs
        Update-SessionPath
    }

    function Test-Running {
        $beatFile = Join-Path $Target 'clipmaschine_data\heartbeat.json'
        if (-not (Test-Path $beatFile)) { return $false }
        try {
            $value = (Get-Content -Path $beatFile -Raw | ConvertFrom-Json).last_cycle
            if ($value -is [DateTime]) {
                $last = [DateTimeOffset]$value.ToUniversalTime()        # PowerShell 7 wandelt selbst um
            } else {
                $last = [DateTimeOffset]::Parse([string]$value, [Globalization.CultureInfo]::InvariantCulture)
            }
            return (([DateTimeOffset]::UtcNow - $last).TotalMinutes -lt 10)
        } catch {
            return $false
        }
    }

    function New-Shortcut([string]$Name, [string]$TargetPath, [string]$Arguments, [string]$Description) {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut((Join-Path $Desktop "$Name.lnk"))
        $link.TargetPath = $TargetPath
        if ($Arguments) { $link.Arguments = $Arguments }
        $link.WorkingDirectory = $Target
        $link.Description = $Description
        $link.Save()
    }

    function Install-Clipmaschine {
        Write-Host 'Clipmaschine - Installation' -ForegroundColor Magenta

        if ($OnWindows) {
            Write-Step 'Pruefe Python und ffmpeg ...'
            if (-not (Test-Python)) {
                Install-WithWinget 'Python.Python.3.12' 'Python' @('--scope', 'user')
                if (-not (Test-Python)) { Install-WithWinget 'Python.Python.3.12' 'Python' @() }
                if (-not (Test-Python)) {
                    throw 'Python wurde installiert, ist aber in diesem Fenster noch nicht nutzbar. Bitte PowerShell schliessen, neu oeffnen und den Befehl erneut ausfuehren.'
                }
            }
            Write-Ok 'Python'
            if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
                Install-WithWinget 'Gyan.FFmpeg' 'ffmpeg' @()
                if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
                    throw 'ffmpeg wurde installiert, ist aber in diesem Fenster noch nicht nutzbar. Bitte PowerShell schliessen, neu oeffnen und den Befehl erneut ausfuehren.'
                }
            }
            Write-Ok 'ffmpeg'
        }

        Write-Step 'Lade die neueste Version ...'
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ('clipmaschine_' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        try {
            $zip = Join-Path $tmp 'clipmaschine.zip'
            Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            $root = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
            if (-not $root) { throw 'Download unvollstaendig - bitte erneut versuchen.' }
            $source = Join-Path $root.FullName 'clipmaschine'
            if (-not (Test-Path (Join-Path $source '__main__.py'))) { throw 'Download unvollstaendig - bitte erneut versuchen.' }
            Write-Ok 'heruntergeladen'

            Write-Step "Installiere nach $Target ..."
            if ($ViaOneDrive) {
                Write-Host '    (Dein Desktop liegt in OneDrive - die Dateien kommen deshalb in einen lokalen Ordner,' -ForegroundColor Yellow
                Write-Host '     auf dem Desktop findest du die Verknuepfungen dazu.)' -ForegroundColor Yellow
            }
            New-Item -ItemType Directory -Path $Target -Force | Out-Null
            $code = Join-Path $Target 'clipmaschine'
            if (Test-Path $code) { Remove-Item -Path $code -Recurse -Force }   # nur Programmcode, keine Daten
            Copy-Item -Path $source -Destination $Target -Recurse -Force
        } finally {
            Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-Ok 'Dateien aktualisiert (config.yaml, .env, Logins und Daten bleiben erhalten)'

        $startScript = Join-Path $Target 'clipmaschine\deploy\start_windows.bat'
        if ($OnWindows) {
            Write-Step 'Erstelle Verknuepfungen auf dem Desktop ...'
            New-Shortcut 'Clipmaschine starten' $startScript '' 'Clipmaschine starten'
            $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $updateArgs = "-NoProfile -ExecutionPolicy Bypass -Command `"irm $SelfUrl | iex; Read-Host 'Enter zum Schliessen'`""
            New-Shortcut 'Clipmaschine aktualisieren' $powershell $updateArgs 'Clipmaschine auf die neueste Version bringen'
            if ($ViaOneDrive) { New-Shortcut 'Clipmaschine (Ordner)' $Target '' 'Ordner der Clipmaschine' }
            Write-Ok 'Clipmaschine starten / Clipmaschine aktualisieren'
        }

        if ($env:CLIPMASCHINE_NO_START) {
            Write-Host "`nFertig - installiert in $Target" -ForegroundColor Green
            return
        }
        if (Test-Running) {
            Write-Host "`nDie Clipmaschine laeuft bereits. Fuer die neue Version ihr Fenster schliessen und" -ForegroundColor Yellow
            Write-Host 'per Doppelklick auf "Clipmaschine starten" neu starten.' -ForegroundColor Yellow
            return
        }
        Write-Step 'Starte die Clipmaschine ...'
        if ($OnWindows) { Start-Process -FilePath $startScript -WorkingDirectory $Target }
        Write-Host "`nFertig! Im neuen Fenster fuehrt dich der Assistent durch die Einrichtung" -ForegroundColor Green
        Write-Host '(Twitch-Kanaele, Twitch-Zugang, TikTok-Anmeldung per QR-Code).' -ForegroundColor Green
    }

    try {
        Install-Clipmaschine
    } catch {
        Write-Host "`nFEHLER: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host 'Kopiere diese Meldung und schick sie mir - ich behebe das.' -ForegroundColor Yellow
    }
}
