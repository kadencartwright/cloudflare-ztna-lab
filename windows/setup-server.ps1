# Run in an elevated PowerShell session on the isolated Windows Server guest.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InterfaceAlias,
    [SecureString]$SharePassword
)
$ErrorActionPreference = 'Stop'
$ip = '192.168.250.30'
$connector = '192.168.250.2'
$account = 'labshare'
$share = 'WinWork'
$path = 'C:\CFZTNA\WinWork'

Set-NetIPInterface -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -Dhcp Disabled
if (-not (Get-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $ip -ErrorAction SilentlyContinue)) {
    New-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $ip -PrefixLength 24 -DefaultGateway $connector
}
Set-DnsClientServerAddress -InterfaceAlias $InterfaceAlias -ServerAddresses '192.168.246.1'
Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True -DefaultInboundAction Block
# Disable the broader built-in SMB inbound rules, including language-neutral rule names.
Get-NetFirewallRule -Name 'FPS-SMB-In-TCP*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
if (-not (Get-NetFirewallRule -Name CFZTNA-SMB -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name CFZTNA-SMB -DisplayName 'CFZTNA connector SMB' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 445 -RemoteAddress $connector -InterfaceAlias $InterfaceAlias -Profile Any
}
Set-SmbServerConfiguration -EnableSMB1Protocol $false -RequireSecuritySignature $true -Force
if (-not (Get-LocalUser -Name $account -ErrorAction SilentlyContinue)) {
    if (-not $SharePassword) {
        $default = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'default-password.txt') -Raw).Trim()
        $SharePassword = ConvertTo-SecureString $default -AsPlainText -Force
    }
    New-LocalUser -Name $account -Password $SharePassword -Description 'CFZTNA disposable share account'
}
$principal = "$env:COMPUTERNAME\$account"
New-Item -Path $path -ItemType Directory -Force | Out-Null
& icacls.exe $path /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' "${principal}:(OI)(CI)M"
if ($LASTEXITCODE) { throw "icacls failed with exit code $LASTEXITCODE" }
if (-not (Get-SmbShare -Name $share -ErrorAction SilentlyContinue)) {
    New-SmbShare -Name $share -Path $path -ChangeAccess $principal -EncryptData $true
}
Get-SmbShare -Name $share | Select-Object Name,Path,EncryptData
