# Run on an enrolled Windows 11 client or check server web egress via the gateway.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('allow','deny','pre-warp','server-egress')][string]$Expected,
    [switch]$WindowsServer,
    [PSCredential]$Credential
)
$ErrorActionPreference = 'Stop'
Start-Transcript -Path (Join-Path $env:TEMP ("cfztna-" + (Get-Date -Format yyyyMMdd-HHmmss) + ".txt"))
try {
    $ip = if ($WindowsServer) { '192.168.250.30' } else { '192.168.250.10' }
    $share = if ($WindowsServer) { 'WinWork' } else { 'Work' }
    Get-NetIPConfiguration | Format-Table InterfaceAlias,IPv4Address,IPv4DefaultGateway
    if ($Expected -ne 'pre-warp') {
        # Gateway's lab certificate chain may have no Schannel revocation endpoint.
        # Keep peer trust and hostname checks, and act on any known revocation.
        $https = @('--noproxy', '*', '--ssl-revoke-best-effort', '--max-time', '15')
        & curl.exe @https -fsS https://example.com/ | Out-Null
        if ($LASTEXITCODE) { throw 'Control request failed TLS verification or HTTP.' }
        $blockHeaders = & curl.exe @https --http1.1 -sS -o NUL -D - https://example.com/cfztna-lab-block
        if ($LASTEXITCODE) { throw 'HTTPS canary request failed.' }
        $blocked = ($blockHeaders | Select-String '^HTTP/1\.1 (\d{3})\b').Matches.Groups[1].Value
        $gatewayRedirect = $blockHeaders -match '^Location: https://blocked\.teams\.cloudflare\.com/\?(?:[^\r\n]*&)?block_reason=cfztna-lab\+HTTPS\+path\+canary(?:&|$)'
        if ($blocked -ne '403' -and ($blocked -ne '302' -or -not $gatewayRedirect)) {
            throw "Expected the Gateway block response for the encrypted canary path, got $blocked"
        }
        Write-Output "HTTPS canary status $blocked with trusted TLS"
    }
    if ($Expected -eq 'server-egress') {
        $route = Find-NetRoute -RemoteIPAddress '1.1.1.1'
        $route | Format-Table InterfaceAlias,DestinationPrefix,NextHop
        if (-not ($route | Where-Object NextHop -eq '192.168.250.3')) {
            throw 'Expected server web egress through 192.168.250.3.'
        }
        Write-Output 'Windows Server HTTPS egress verified.'
        return
    }
    $routes = Find-NetRoute -RemoteIPAddress $ip
    $routes | Format-Table InterfaceAlias,DestinationPrefix,NextHop
    if ($Expected -ne 'pre-warp' -and -not ($routes | Where-Object InterfaceAlias -eq CloudflareWARP)) {
        throw 'The private route did not select the Cloudflare WARP interface.'
    }
    $reachable = Test-NetConnection $ip -Port 445 -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($reachable -ne ($Expected -eq 'allow')) { throw "Unexpected SMB reachability: $reachable" }
    if ($Expected -eq 'allow') {
        if (-not $Credential) {
            $Credential = Get-Credential -Message 'Enter the Samba or Windows Server share logon'
        }
        $root = "\\$ip\$share"
        $drive = New-PSDrive -Name ZTLab -PSProvider FileSystem -Root $root -Credential $Credential
        $nonce = [Guid]::NewGuid().ToString()
        $file = "$root\$nonce.txt"
        try {
            [IO.File]::WriteAllText($file, $nonce)
            if ([IO.File]::ReadAllText($file) -ne $nonce) { throw 'SMB read/write contents differ.' }
            $connections = Get-SmbConnection | Where-Object { $_.ServerName -eq $ip -and $_.ShareName -eq $share }
            $connections | Format-Table ServerName,ShareName,Dialect,Encrypted
            if (-not ($connections | Where-Object Encrypted)) { throw 'No encrypted SMB connection found for the share.' }
            Write-Output "SMB read/write verified for $share"
        } finally {
            try {
                if ([IO.File]::Exists($file)) { [IO.File]::Delete($file) }
            } finally {
                Remove-PSDrive $drive.Name
            }
        }
    }
    if (-not $WindowsServer) {
        $ipp = Test-NetConnection $ip -Port 631 -InformationLevel Quiet -WarningAction SilentlyContinue
        if ($ipp -ne ($Expected -eq 'allow')) { throw "Unexpected IPP reachability: $ipp" }
        if ($ipp) {
            Write-Output "IPP listens at http://${ip}:631/ipp/print. Use Add-Printer -IppURL and verify a real job on the NixOS origin."
        }
    }
} finally {
    Stop-Transcript
}
