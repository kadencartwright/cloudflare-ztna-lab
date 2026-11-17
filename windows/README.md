# Windows 11 client and Windows Server 2025

Use installation ISOs for which you have a license or download time-limited Microsoft evaluation media. This project does not redistribute Windows. [Windows 11 Enterprise evaluation](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise) · [Windows Server 2025 evaluation](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-server-2025)

1. Run `just up`. Put the ISOs in a libvirt-readable directory, such as `/var/lib/libvirt/images/cfztna-media`, with read/traverse access for the QEMU runtime user. Keep your home directory private.
2. Start the guests and open each console. Both definitions use Q35, Microsoft-key UEFI Secure Boot, a software TPM 2.0, SATA storage and an Intel NIC. Windows 11 uses DHCP. The server resources switch has no DHCP. Install Windows and create disposable local users as supported by your edition.

   ```sh
   just windows-up client /var/lib/libvirt/images/cfztna-media/Win11.iso
   just windows-up server /var/lib/libvirt/images/cfztna-media/Server2025.iso
   virt-viewer --connect qemu:///system cfztna-win-client
   virt-viewer --connect qemu:///system cfztna-win-server
   ```

3. Transfer the checked-in PowerShell scripts using a read-only ISO. For example, build with `mkisofs -o /tmp/cfztna-windows-scripts.iso -J -R windows` and copy it to the libvirt media directory. After installation attach it using `virsh -c qemu:///system change-media cfztna-win-server sdb /var/lib/libvirt/images/cfztna-media/cfztna-windows-scripts.iso --update --live`. If the drive is empty use `--insert`. Repeat for the client.
4. In an elevated Server PowerShell, inspect `Get-NetAdapter`. Run `setup-server.ps1 -InterfaceAlias Ethernet` with the exact lab adapter name. It configures 192.168.250.30/24, a connector-only inbound TCP/445 rule, the `labshare` local user, and an encrypted `WinWork` share. Choose a separate password. Verify `just ssh connector 'nc -w 4 -vz 192.168.250.30 445'`. Set `expose_windows_server = true` in the dedicated Cloudflare variables and review/apply the new plan.
5. In Windows 11, install the official Cloudflare One Client MSI, enroll in the lab as the employee, and trust the verified Gateway root in the guest. Create the protected device marker from elevated PowerShell using `New-Item C:\ProgramData\cfztna-managed -ItemType File -Force`. Verify the profile and posture result in the dashboard. [Official Windows client](https://developers.cloudflare.com/cloudflare-one/team-and-resources/devices/cloudflare-one-client/download/)
6. Run `test-client.ps1 -Expected allow` and `test-client.ps1 -Expected allow -WindowsServer`. Enter the Samba lab login or `WINDOWS-SERVER-NAME\labshare` when prompted. The script verifies the HTTPS path and same-host control, routes, and SMB write/read, then saves a transcript to the guest's temp directory. Use `-Expected pre-warp` before enrollment or `-Expected deny` after a posture or identity change. This is an isolated, IP-based standalone SMB test.
7. For native IPP try `Add-Printer -Name 'CFZTNA IPP' -IppURL 'http://192.168.250.10:631/ipp/print'` from Windows 11. Print a document and inspect `just ssh origin 'sudo ls -lt /var/lib/cfztna-print/'`. The Linux test sends a PDF through the same IPP endpoint and compares its hash. [PowerShell Add-Printer](https://learn.microsoft.com/en-us/powershell/module/printmanagement/add-printer)
8. Shut down the client and run `just windows-move home`. Repeat the checks with the same MAC, Windows instance and WARP registration. Use `just windows-move office` after shutting down to return. Restart an existing stopped VM with `just windows-up client` or `just windows-up server` without an ISO path. Shut down both guests before running `just windows-down client` and `just windows-down server`.

Use the lab-only firewall and share on the isolated server NIC. This runbook assumes English Windows for the server's built-in administrative ACL names. There is no Windows MDM, Windows fail-closed enforcement, or AD domain. Native printer-driver behavior requires a real guest test.
