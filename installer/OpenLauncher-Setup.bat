@echo off
rem OpenLauncher setup (Open CAFD Map): installs or upgrades OpenLauncher from the latest GitHub release.
rem Double-click it. No admin rights or git needed. Re-run it any time to upgrade (your workspace and
rem settings are kept). The PowerShell part below the marker does the work.
rem
rem Unattended / offline use (optional environment variables):
rem   OCM_SETUP_DIR=<folder>   install folder (default %LOCALAPPDATA%\OpenCAFDMap)
rem   OCM_SETUP_ZIP=<file>     install from a downloaded OpenCAFDMap.zip instead of GitHub
rem   OCM_SETUP_TAG=<tag>      install that release (e.g. v2.3.11, or a v3 preview v3.1.0-rc.1) instead of the latest
rem An existing OpenLauncher v2 install is asked whether to upgrade to v3 or stay on v2 (updated unattended
rem from OpenLauncher v2, it stays on v2).
rem   OCM_SETUP_QUIET=1        no questions (Python is not installed automatically in quiet mode)
rem   OCM_SETUP_NO_SHORTCUTS=1 / OCM_SETUP_NO_LAUNCH=1
setlocal
set "OCM_SETUP_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:OCM_SETUP_SELF); Invoke-Expression $s.Substring($s.IndexOf('#'+'PS-START'))"
set "RC=%ERRORLEVEL%"
if not "%OCM_SETUP_QUIET%"=="1" pause
exit /b %RC%

#PS-START
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repo = 'shamair28/OpenCAFDMap'          # fixed on purpose: setup only installs from this repository
$Quiet = $env:OCM_SETUP_QUIET -eq '1'
$Keep = @('workspace', 'config.json')     # never touched by an upgrade

function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Ask($q, $default) {
    if ($Quiet) { return $default }
    $a = Read-Host "$q [$default]"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return $a.Trim() }
}
function Fail($t) { Say "`n$t" 'Red'; exit 1 }

function Find-Python {
    # a real Python 3.10+ with tkinter (the Microsoft Store "python" stub doesn't count)
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $cands += ,@('py', '-3') }
    if (Get-Command python -ErrorAction SilentlyContinue) { $cands += ,@('python') }
    foreach ($d in (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*", "$env:ProgramFiles\Python3*" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
        $exe = Join-Path $d.FullName 'python.exe'
        if (Test-Path $exe) { $cands += ,@($exe) }
    }
    foreach ($c in $cands) {
        try {
            $args2 = @($c | Select-Object -Skip 1) + @('-c', 'import sys, tkinter; print(sys.executable); print(sys.version_info >= (3, 10))')
            $out = & $c[0] @args2 2>$null
            if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2 -and $out[1] -eq 'True') { return $out[0] }
        } catch { }
    }
    return $null
}

Say "OpenLauncher setup - Open CAFD Map" 'Cyan'
Say "Readable coding names for E-Sys, built from your own psdzdata. https://github.com/$Repo`n"

# ---- install folder
$default = if ($env:OCM_SETUP_DIR) { $env:OCM_SETUP_DIR } else { Join-Path $env:LOCALAPPDATA 'OpenCAFDMap' }
$dir = [IO.Path]::GetFullPath((Ask 'Install folder' $default))
$upgrade = Test-Path (Join-Path $dir 'openlauncher.py')
if (Test-Path (Join-Path $dir '.git')) { Fail "$dir is a git clone - update it with 'git pull' instead." }
if ((Test-Path $dir) -and -not $upgrade -and (Get-ChildItem $dir -Force | Select-Object -First 1)) {
    Fail "$dir exists and isn't an OpenLauncher install. Choose an empty or new folder."
}

# ---- Python
$py = Find-Python
if (-not $py) {
    Say "Python 3.10+ (with tkinter) was not found. OpenLauncher needs it." 'Yellow'
    if (-not $Quiet -and (Get-Command winget -ErrorAction SilentlyContinue) -and
        ((Ask 'Install Python 3.12 now with winget? (y/n)' 'y') -match '^[yY]')) {
        winget install -e --id Python.Python.3.12 --scope user
        $py = Find-Python
    }
    if (-not $py) { Fail "Install Python from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run setup again." }
}
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
Say "Python: $py"

# ---- which release
# '' = GitHub's latest release (OpenLauncher v3). The copy of this script on the v2 branch (attached to v2
# releases) says 'v2', so it installs and upgrades OpenLauncher v2.
$Channel = ''
function Get-NewestStable($major) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $best = $null
    foreach ($r in (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases?per_page=100" -UseBasicParsing)) {
        if ($r.draft -or $r.prerelease -or $r.tag_name -notmatch "^v$major\.\d+\.\d+$") { continue }
        $v = [version]$r.tag_name.Substring(1)
        if (-not $best -or $v -gt $best) { $best = $v }
    }
    if ($best) { return "v$best" } else { return $null }
}
$tag = $env:OCM_SETUP_TAG
if (-not $tag -and -not $env:OCM_SETUP_ZIP) {
    $installed = if ($upgrade -and (Test-Path (Join-Path $dir 'VERSION'))) { (Get-Content (Join-Path $dir 'VERSION') -Raw).Trim().TrimStart('v') } else { '' }
    $major = $null
    if ($Channel -eq 'v2') {
        $major = 2
    } elseif ($installed -match '^2\.\d+\.\d+$') {
        if ($Quiet -and $env:OCM_SETUP_DIR) {
            # OpenLauncher v2.3.10 and older update themselves with the latest release's setup and no tag: keep them on v2
            $major = 2
        } elseif ((Ask "This folder has OpenLauncher v$installed. Upgrade to v3 (3) or keep v2 and update it (2)?" '3') -match '^\s*2') {
            $major = 2
        }
    }
    if ($major) {
        try { $tag = Get-NewestStable $major } catch { Fail "Couldn't look up the newest OpenLauncher v$major release on GitHub ($($_.Exception.Message)). Nothing was installed." }
        if (-not $tag) { Fail "There's no OpenLauncher v$major release on GitHub. Nothing was installed." }
        Say "Staying on OpenLauncher v${major}: installing $tag"
    }
}

# ---- get the release
$tmp = Join-Path $env:TEMP ("ocm-setup-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'OpenCAFDMap.zip'
    if ($env:OCM_SETUP_ZIP) {
        Say "Using $env:OCM_SETUP_ZIP"
        Copy-Item $env:OCM_SETUP_ZIP $zip
    } else {
        if ($tag) {
            # a specific release of this repository only (OpenLauncher's updater always passes the exact tag)
            if ($tag -notmatch '^v\d+\.\d+\.\d+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$') {
                Fail "OCM_SETUP_TAG '$tag' is not a release tag. Nothing was installed."
            }
            $base = "https://github.com/$Repo/releases/download/$tag"
            Say "Downloading release $tag from github.com/$Repo ..."
        } else {
            $base = "https://github.com/$Repo/releases/latest/download"
            Say "Downloading the latest release from github.com/$Repo ..."
        }
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest "$base/OpenCAFDMap.zip" -OutFile $zip -UseBasicParsing
        # saved to a file: GitHub serves it as application/octet-stream, which Windows PowerShell 5.1
        # would otherwise hand back as raw bytes instead of text
        Invoke-WebRequest "$base/OpenCAFDMap.zip.sha256" -OutFile "$zip.sha256" -UseBasicParsing
        $want = ((Get-Content "$zip.sha256" -Raw).Trim() -split '\s+')[0].ToLower()
        $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
        if ($want -ne $got) { Fail "Checksum mismatch - the download is damaged. Nothing was installed." }
        Say "Checksum OK"
    }
    $new = Join-Path $tmp 'new'
    Expand-Archive $zip $new
    if (-not (Test-Path (Join-Path $new 'openlauncher.py'))) { Fail "That zip isn't an OpenLauncher release." }
    $version = if (Test-Path (Join-Path $new 'VERSION')) { (Get-Content (Join-Path $new 'VERSION') -Raw).Trim() } else { '?' }

    # ---- install / upgrade (workspace\ and config.json are kept)
    New-Item -ItemType Directory $dir -Force | Out-Null
    if ($upgrade) {
        Say "Upgrading $dir to $version (keeping your workspace and settings)"
        Get-ChildItem $dir -Force | Where-Object { $Keep -notcontains $_.Name } | Remove-Item -Recurse -Force
    } else {
        Say "Installing OpenLauncher $version to $dir"
    }
    Get-ChildItem $new -Force | Where-Object { $Keep -notcontains $_.Name } | Copy-Item -Destination $dir -Recurse -Force
    Get-ChildItem $dir -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# ---- shortcuts
if ($env:OCM_SETUP_NO_SHORTCUTS -ne '1') {
    $sh = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
    foreach ($p in $places) {
        $l = $sh.CreateShortcut((Join-Path $p 'OpenLauncher.lnk'))
        $l.TargetPath = $pyw
        $l.Arguments = '"' + (Join-Path $dir 'openlauncher.py') + '"'
        $l.WorkingDirectory = $dir
        $l.IconLocation = (Join-Path $dir 'assets\openlauncher.ico')
        $l.Description = 'OpenLauncher - Open CAFD Map'
        $l.Save()
    }
    Say "Shortcuts: Desktop and Start menu (OpenLauncher)"
}

Say "`nDone. OpenLauncher $version is installed in $dir" 'Green'
Say "Run setup again any time to upgrade. To uninstall: delete that folder and the OpenLauncher shortcuts."
if ($env:OCM_SETUP_NO_LAUNCH -ne '1' -and ((Ask 'Start OpenLauncher now? (y/n)' 'y') -match '^[yY]')) {
    Start-Process $pyw -ArgumentList ('"' + (Join-Path $dir 'openlauncher.py') + '"') -WorkingDirectory $dir
}
exit 0
