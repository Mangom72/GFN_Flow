param(
    [string]$MsysRoot,
    [switch]$StartOnSignIn,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
if (!$MsysRoot) {
    $MsysRoot = @("$env:LOCALAPPDATA\MacroStudioRuntime\msys64", 'C:\msys64') |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ 'ucrt64\bin\pythonw.exe') } |
        Select-Object -First 1
}
if (!$MsysRoot) { throw 'Install the MSYS2 UCRT64 dependencies listed in README.md, then pass -MsysRoot.' }
$runtime = Join-Path $MsysRoot 'ucrt64\bin'
$python = Join-Path $runtime 'python.exe'
$pythonw = Join-Path $runtime 'pythonw.exe'
$env:PATH = "$runtime;$env:PATH"
& $python -c "import gi, numpy, PIL; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"
if ($LASTEXITCODE -or !(Test-Path -LiteralPath $pythonw)) { throw 'The MSYS2 UCRT64 dependencies are incomplete.' }

$install = Join-Path $env:LOCALAPPDATA 'Programs\GFNFlow'
$app = Join-Path $install ('app-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff'))
[void][IO.Directory]::CreateDirectory($app)
Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object {
    $_.Extension -in '.py','.pyw' -or $_.Name -in 'ui_tokens.json','ui_ko.json','windows_hotspot.ps1','LICENSE','THIRD_PARTY_NOTICES.md'
} | Copy-Item -Destination $app
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'assets') -Destination $app -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'licenses') -Destination $app -Recurse
[void][IO.Directory]::CreateDirectory((Join-Path $app 'login'))
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'login\gfn_login.py') -Destination (Join-Path $app 'login')
& $python (Join-Path $PSScriptRoot 'tools\fetch_font.py') (Join-Path $app 'assets\fonts')
if ($LASTEXITCODE) { throw 'Could not prepare the Pretendard font.' }

$entry = Join-Path $app 'windows_entry.pyw'
$shell = New-Object -ComObject WScript.Shell
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'GFN Flow.lnk'
$links = @{ (Join-Path ([Environment]::GetFolderPath('Programs')) 'GFN Flow.lnk') = 'manage' }
if ($StartOnSignIn -or (Test-Path -LiteralPath $startup)) { $links[$startup] = 'daemon' }
foreach ($link in $links.GetEnumerator()) {
    $shortcut = $shell.CreateShortcut($link.Key)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '-X utf8 "' + $entry + '" ' + $link.Value
    $shortcut.WorkingDirectory = $app
    $shortcut.Description = 'GFN Flow macro library'
    $shortcut.Save()
}
if ($Launch) { Start-Process -FilePath $pythonw -ArgumentList ('-X utf8 "' + $entry + '" manage') -WorkingDirectory $app -WindowStyle Hidden }
Write-Output 'GFN Flow installed. Open GFN Flow from Start.'
