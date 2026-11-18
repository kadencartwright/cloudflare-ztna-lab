package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"

	tea "charm.land/bubbletea/v2"
)

var titles = map[string]string{
	"office":      "Office workstation",
	"remote":      "Home workstation",
	"connector":   "Cloudflare Tunnel connector",
	"origin":      "Samba and print server",
	"iot-gateway": "IoT gateway",
}

type topology struct {
	Machines map[string]struct {
		IP string `json:"ip"`
	} `json:"machines"`
	WindowsServer string         `json:"windows_server_ip"`
	Ports         map[string]int `json:"service_ports"`
	IoT           struct {
		Gateway string `json:"gateway"`
	} `json:"iot"`
}

func loadTopology(path string) (topology, error) {
	var lab topology
	data, err := os.ReadFile(path)
	if err == nil {
		err = json.Unmarshal(data, &lab)
	}
	if err != nil {
		return topology{}, fmt.Errorf("read lab topology: %w", err)
	}
	for _, address := range []string{lab.Machines["origin"].IP, lab.WindowsServer, lab.IoT.Gateway} {
		if net.ParseIP(address).To4() == nil {
			return topology{}, fmt.Errorf("invalid IPv4 address in lab topology: %q", address)
		}
	}
	for _, service := range []string{"smb", "ipp"} {
		if port := lab.Ports[service]; port < 1 || port > 65535 {
			return topology{}, fmt.Errorf("invalid %s port in lab topology", service)
		}
	}
	return lab, nil
}

func main() {
	role := flag.String("role", "", "lab guest role (office, remote, connector, origin, iot-gateway)")
	path := flag.String("topology", "/etc/cfztna-console/topology.json", "path to the lab topology JSON")
	flag.Parse()
	if _, ok := titles[*role]; !ok || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "Choose a lab role with --role.")
		flag.Usage()
		os.Exit(2)
	}
	lab, err := loadTopology(*path)
	if err == nil {
		ctx, cancel := context.WithCancel(context.Background())
		_, err = tea.NewProgram(newModel(ctx, titles[*role], actionsFor(*role, lab)), tea.WithContext(ctx)).Run()
		cancel()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
