import ipaddress
import json
from pathlib import Path
import unittest

from tools.windows import domain_xml, NETWORKS

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = json.loads((ROOT / "topology.json").read_text())


class TopologyTests(unittest.TestCase):
    def test_networks_disjoint(self):
        office, home, resources = (
            ipaddress.ip_network(TOPOLOGY[segment]["cidr"])
            for segment in ("office", "home", "resources")
        )
        self.assertFalse(office.overlaps(home))
        self.assertFalse(office.overlaps(resources))
        self.assertFalse(home.overlaps(resources))
        self.assertIn(ipaddress.ip_address(TOPOLOGY["windows_server_ip"]), resources)

    def test_static_address_and_dhcp_separation(self):
        machines = TOPOLOGY["machines"]
        self.assertEqual(len({vm["ip"] for vm in machines.values()}), len(machines))
        self.assertEqual(len({vm["mac"] for vm in machines.values()}), len(machines))
        for machine in machines.values():
            network = ipaddress.ip_network(TOPOLOGY[machine["segment"]]["cidr"])
            address = ipaddress.ip_address(machine["ip"])
            self.assertIn(address, network)
            self.assertNotEqual(str(address), TOPOLOGY[machine["segment"]]["gateway"])
            self.assertLess(int(address) - int(network.network_address), 100)

    def test_windows_domain_media_path_and_isolation(self):
        for role, network in (("server", "server"), ("client", "home"), ("client", "office")):
            xml = domain_xml(role, '/tmp/my iso & docs.iso', network, '/pool/disk.qcow2')
            self.assertEqual(xml.find("devices/interface/source").get("network"), NETWORKS[network])
            self.assertEqual(len(xml.findall("devices/interface")), 1)
            self.assertEqual(xml.find("devices/disk[@device='cdrom']/source").get("file"), '/tmp/my iso & docs.iso')
            self.assertEqual(xml.find("devices/tpm/backend").get("version"), "2.0")
            self.assertEqual(xml.find("devices/graphics").get("listen"), "127.0.0.1")


class UnattendedTests(unittest.TestCase):
    def test_answer_media_is_read_only_and_keeps_one_writable_disk(self):
        xml = domain_xml("client", "/pool/install.iso", "office", "/pool/client.qcow2", "/pool/answer.iso")
        disks = xml.findall("devices/disk")
        self.assertEqual(sum(disk.find("readonly") is None for disk in disks), 1)
        self.assertEqual(disks[-1].find("target").get("dev"), "sdc")

    def test_answer_xml_uses_expected_setup_passes_and_image_index(self):
        import xml.etree.ElementTree as ET

        from tools.windows_answer import answer_xml

        for role, index, account in (("client", 6, "LocalAccount"), ("server", 2, "AdministratorPassword")):
            xml = ET.fromstring(answer_xml(role, index, "synthetic-password"))
            namespace = {"u": "urn:schemas-microsoft-com:unattend"}
            self.assertEqual([item.get("pass") for item in xml], ["windowsPE", "specialize", "oobeSystem"])
            self.assertEqual(xml.findtext(".//u:MetaData/u:Value", namespaces=namespace), str(index))
            self.assertIsNotNone(xml.find(f".//u:{account}", namespaces=namespace))
