"""Create per-VM Windows Setup media without putting credentials in Git."""
import os
import secrets
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

MEDIA = Path("/var/lib/libvirt/images/cfztna-media")
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".lab"


def initial_password():
    return os.environ.get("CFZTNA_WINDOWS_PASSWORD") or (ROOT / "windows/default-password.txt").read_text().strip()

DISK = """
<DiskConfiguration>
  <Disk wcm:action="add">
    <DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>
    <CreatePartitions>
      <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>260</Size></CreatePartition>
      <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
      <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
    </CreatePartitions>
    <ModifyPartitions>
      <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format></ModifyPartition>
      <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Letter>C</Letter></ModifyPartition>
    </ModifyPartitions>
  </Disk>
  <WillShowUI>OnError</WillShowUI>
</DiskConfiguration>
"""


def answer_xml(role, image_index, password):
    component = 'processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"'
    credentials = f"<Password><Value>{escape(password)}</Value><PlainText>true</PlainText></Password>"
    product_key = ""
    if role == "client":
        # Microsoft's published Windows Pro setup key selects Pro. Activation is separate.
        product_key = "<ProductKey><Key>W269N-WFGWX-YVC9B-4J6C9-T83GX</Key><WillShowUI>OnError</WillShowUI></ProductKey>"
        accounts = f"""<LocalAccounts><LocalAccount wcm:action="add">
          {credentials}<Name>LabAdmin</Name><Group>Administrators</Group>
        </LocalAccount></LocalAccounts>"""
    else:
        accounts = credentials.replace("Password>", "AdministratorPassword>")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" {component}>
      <SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>
      <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale>
      <UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" {component}>
      {DISK}
      <ImageInstall><OSImage>
        <InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/INDEX</Key><Value>{image_index}</Value></MetaData></InstallFrom>
        <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
        <WillShowUI>OnError</WillShowUI>
      </OSImage></ImageInstall>
      <UserData>{product_key}<AcceptEula>true</AcceptEula></UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" {component}>
      <ComputerName>CFZTNA-{role.upper()}</ComputerName>
      <TimeZone>UTC</TimeZone>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" {component}>
      <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale>
      <UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Shell-Setup" {component}>
      <UserAccounts>{accounts}</UserAccounts>
      <OOBE><HideEULAPage>true</HideEULAPage><HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <HideLocalAccountScreen>{str(role == "server").lower()}</HideLocalAccountScreen>
        <ProtectYourPC>3</ProtectYourPC></OOBE>
    </component>
  </settings>
</unattend>
"""


def make_media(role, image_index, capabilities):
    root = ET.fromstring(capabilities)
    label = root.findtext("host/secmodel[model='dac']/baselabel[@type='kvm']")
    if not label:
        raise SystemExit("Cannot determine the QEMU DAC user for private answer media.")
    uid, gid = (int(value) for value in label.split(":"))
    STATE.mkdir(mode=0o700, exist_ok=True)
    os_name = "LabAdmin" if role == "client" else "Administrator"
    password = initial_password()
    tag = secrets.token_hex(6)
    credential = STATE / f"windows-{role}-{tag}.credentials"
    with private_file(credential) as stream:
        stream.write(f"Username: {os_name}\nPassword: {password}\n")
    with tempfile.TemporaryDirectory(dir=STATE) as directory:
        scratch = Path(directory)
        answer = scratch / "Autounattend.xml"
        answer.write_text(answer_xml(role, image_index, password))
        iso = scratch / "answer.iso"
        subprocess.run(["mkisofs", "-quiet", "-J", "-R", "-o", str(iso), str(answer)], check=True)
        target = MEDIA / f"cfztna-{role}-{tag}-answer.iso"
        subprocess.run(["sudo", "install", "-m", "0600", "-o", str(uid), "-g", str(gid), str(iso), str(target)], check=True)
    print(f"Initial Windows login is in {credential}", flush=True)
    return target


def private_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
