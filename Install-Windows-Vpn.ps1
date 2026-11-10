param([string]$OwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell as administrator for this one-time VPN setup.'
}
$owner = [Security.Principal.SecurityIdentifier]::new($OwnerSid)
if ($owner.Value -notmatch '^S-1-(5-21|12-1)-') { throw 'The allowed client must be a Windows user.' }
$root = Join-Path $env:ProgramFiles 'GFNFlowVpn'
$serviceName = 'GFNFlowVpn'
if ((Test-Path -LiteralPath $root) -or (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)) {
    throw 'The GFN Flow VPN service is already installed or a previous installation needs repair.'
}
$wg = Join-Path $env:ProgramFiles 'WireGuard\wireguard.exe'
if (!(Test-Path -LiteralPath $wg -PathType Leaf)) { throw 'Install WireGuard for Windows first.' }
$signature = Get-AuthenticodeSignature -LiteralPath $wg
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'WireGuard') {
    throw 'WireGuard did not pass publisher verification.'
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'The Windows .NET Framework compiler is unavailable.' }
$files = @('windows_vpn_service_worker.py','windows_vpn.py','windows_vpn_profiles.py','windows_host.py',
           'windows_input.py','detection.py','vpn_servers.py','macro_library.py','input_activity.py','tool_status.py')
foreach ($file in $files + @('platform\windows\VpnService.cs','login\gfn_login.py')) {
    if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot $file) -PathType Leaf)) {
        throw "The distribution is missing $file."
    }
}

[void][IO.Directory]::CreateDirectory($root)
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$administrators = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$acl.SetOwner($administrators)
foreach ($sid in 'S-1-5-18','S-1-5-32-544') {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl',
        'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $root -AclObject $acl
$app = Join-Path $root 'app'
$pythonDir = Join-Path $root 'python'
[void][IO.Directory]::CreateDirectory((Join-Path $app 'login'))
[void][IO.Directory]::CreateDirectory($pythonDir)
[void][IO.Directory]::CreateDirectory((Join-Path $root 'data\MacroStudioNative\vpn'))

$zip = Join-Path $root 'runtime.zip'
Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip' -OutFile $zip
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159') {
    throw 'The Python runtime checksum did not match.'
}
Expand-Archive -LiteralPath $zip -DestinationPath $pythonDir
Remove-Item -LiteralPath $zip
[IO.File]::WriteAllLines((Join-Path $pythonDir 'python314._pth'), @('python314.zip', '.', '..\app'), [Text.Encoding]::ASCII)
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $app
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'login\gfn_login.py') -Destination (Join-Path $app 'login')
$python = Join-Path $pythonDir 'python.exe'
& $python -I -B -X utf8 -c 'import windows_vpn_service_worker'
if ($LASTEXITCODE) { throw 'The VPN worker failed to load.' }
[IO.File]::WriteAllText((Join-Path $root 'owner.sid'), $owner.Value, [Text.Encoding]::ASCII)
$exe = Join-Path $root 'GFNFlowVpn.exe'
& $compiler /nologo /platform:x64 /target:exe "/out:$exe" /reference:System.ServiceProcess.dll (Join-Path $PSScriptRoot 'platform\windows\VpnService.cs')
if ($LASTEXITCODE) { throw 'Could not build the VPN service.' }
foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
    $itemAcl = Get-Acl -LiteralPath $item.FullName
    if ($itemAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin 'S-1-5-18','S-1-5-32-544') {
        $itemAcl.SetOwner($administrators)
        Set-Acl -LiteralPath $item.FullName -AclObject $itemAcl
    }
}
$service = @{Name=$serviceName; BinaryPathName=('"' + $exe + '"'); DisplayName='GFN Flow VPN'; StartupType='Automatic'}
New-Service @service -Description 'Manages the WireGuard routes used by GFN Flow.' | Out-Null
Start-Service -Name $serviceName
Write-Output 'VPN service installed. Add a WireGuard profile in GFN Flow Settings.'
