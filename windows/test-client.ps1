# Run from Windows 11 after enrolling the Cloudflare One Client.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('allow','deny','pre-warp')][string]$Expected,
    [switch]$WindowsServer
)
$ErrorActionPreference = 'Stop'
Start-Transcript -Path (Join-Path $env:TEMP ("cfztna-" + (Get-Date -Format yyyyMMdd-HHmmss) + ".txt"))
try {
    $ip = if ($WindowsServer) { '192.168.250.30' } else { '192.168.250.10' }
    $share = if ($WindowsServer) { 'WinWork' } else { 'Work' }
    Get-NetIPConfiguration | Format-Table InterfaceAlias,IPv4Address,IPv4DefaultGateway
    if ($Expected -ne 'pre-warp') {
        & curl.exe --noproxy '*' --max-time 15 -fsS https://example.com/ | Out-Null
        if ($LASTEXITCODE) { throw 'Control request failed TLS verification or HTTP.' }
        $blocked = & curl.exe --noproxy '*' --max-time 15 -sS -o NUL -w '%{http_code}' https://example.com/cfztna-lab-block
        if ($LASTEXITCODE -or $blocked -ne '403') { throw "Expected HTTP 403 for the encrypted canary path, got $blocked" }
        Write-Output "HTTPS canary status $blocked with trusted TLS"
    }
    Find-NetRoute -RemoteIPAddress $ip | Format-Table
    $reachable = Test-NetConnection $ip -Port 445 -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($reachable -ne ($Expected -eq 'allow')) { throw "Unexpected SMB reachability: $reachable" }
    if ($Expected -eq 'allow') {
        $credential = Get-Credential -Message 'Enter the Samba or Windows Server share logon'
        $drive = New-PSDrive -Name ZTLab -PSProvider FileSystem -Root "\\$ip\$share" -Credential $credential
        $nonce = [Guid]::NewGuid().ToString()
        $file = Join-Path "$($drive.Name):\" "$nonce.txt"
        try {
            Set-Content -Path $file -Value $nonce
            if ((Get-Content $file -Raw).Trim() -ne $nonce) { throw 'SMB read/write contents differ.' }
            Get-SmbConnection | Where-Object ShareName -eq $share | Format-Table ServerName,ShareName,Dialect,Encrypted
            Write-Output "SMB read/write verified for $share"
        } finally {
            Remove-Item $file -ErrorAction SilentlyContinue
            Remove-PSDrive $drive.Name
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
