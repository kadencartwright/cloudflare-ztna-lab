# Run elevated via Windows guest provisioning. Inject the three CFZTNA_WARP
# variables at runtime from your secret store.
#Requires -RunAsAdministrator
param([Parameter(Mandatory)][string]$MsiPath)
$ErrorActionPreference = 'Stop'
$team = $env:CFZTNA_WARP_TEAM
$id = $env:CFZTNA_WARP_CLIENT_ID
$secret = $env:CFZTNA_WARP_CLIENT_SECRET
if ($team -cnotmatch '^[a-z0-9][a-z0-9-]*$' -or
    $id -notmatch '^[a-zA-Z0-9._-]+$' -or $secret -notmatch '^[a-zA-Z0-9._-]+$') {
    throw 'Supply the lab team and device service-token credentials through the environment.'
}

$msi = (Resolve-Path -LiteralPath $MsiPath).Path
$signature = Get-AuthenticodeSignature -LiteralPath $msi
$publisher = '(?i)(?:^|,\s*)O=(?:Cloudflare|"Cloudflare, Inc\."|Cloudflare\\, Inc\.)(?=,|$)'
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $publisher) {
    throw 'Provide a valid Cloudflare-signed MSI.'
}

$folder = Join-Path $env:ProgramData 'Cloudflare'
New-Item -Type Directory -Path $folder -Force | Out-Null
$file = Join-Path $folder 'mdm.xml'
$temp = Join-Path $folder ([Guid]::NewGuid().ToString('N') + '.tmp')
try {
    New-Item -Type File -Path $temp | Out-Null
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetSecurityDescriptorSddlForm('D:P(A;;FA;;;SY)(A;;FA;;;BA)')
    Set-Acl -LiteralPath $temp -AclObject $acl
    @"
<dict>
  <key>organization</key><string>$team</string>
  <key>auth_client_id</key><string>$id</string>
  <key>auth_client_secret</key><string>$secret</string>
  <key>onboarding</key><false/>
  <key>auto_connect</key><integer>1</integer>
  <key>switch_locked</key><true/>
</dict>
"@ | Set-Content -LiteralPath $temp -Encoding ASCII
    if ([IO.File]::Exists($file)) {
        Set-Acl -LiteralPath $file -AclObject $acl
        [IO.File]::Replace($temp, $file, $null, $false)
        Set-Acl -LiteralPath $file -AclObject $acl
    } else {
        [IO.File]::Move($temp, $file)
    }
    $marker = Join-Path $env:ProgramData 'cfztna-managed'
    New-Item -Type File -Path $marker -Force | Out-Null
    Set-Acl -LiteralPath $marker -AclObject $acl
    $process = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /quiet /norestart" -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "WARP MSI failed with exit code $($process.ExitCode)."
    }
    if ($process.ExitCode -eq 3010) {
        Write-Output 'Restart Windows to complete the MSI installation.'
    }
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
}
