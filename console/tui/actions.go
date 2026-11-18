package main

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/charmbracelet/x/ansi"
)

type action struct {
	label    string
	commands [][]string
	impact   string
}

func check(label string, commands ...[]string) action {
	return action{label: label, commands: commands}
}

func status(units ...string) []string {
	return append([]string{"systemctl", "status", "--no-pager", "--lines=0"}, units...)
}

func logs(unit string) []string {
	return []string{"sudo", "-n", "journalctl", "--no-pager", "-n", "25", "-u", unit}
}

func restart(label, unit, impact string) action {
	return action{
		label:    "Restart " + label,
		commands: [][]string{{"sudo", "-n", "systemctl", "restart", unit}, status(unit)},
		impact:   impact,
	}
}

func portCheck(address string, port int) []string {
	return []string{"nc", "-4", "-v", "-z", "-w", "4", address, strconv.Itoa(port)}
}

func actionsFor(role string, lab topology) []action {
	origin := lab.Machines["origin"].IP
	smb := portCheck(origin, lab.Ports["smb"])
	ipp := portCheck(origin, lab.Ports["ipp"])
	warp := []string{"warp-cli", "--accept-tos", "status"}
	warpRestart := restart("WARP", "cloudflare-warp.service", "Traffic through WARP will pause briefly.")
	switch role {
	case "office", "remote":
		return []action{
			check("WARP status and private route", warp, []string{"ip", "-4", "route", "get", origin}),
			check("Check Samba and IPP TCP ports", smb, ipp),
			check("Check HTTPS using the Gateway CA", []string{
				"curl", "--noproxy", "*", "-4", "--max-time", "10", "--silent", "--show-error", "--fail",
				"--cacert", "/etc/cfztna/gateway-ca.pem", "-o", "/dev/null", "-w", "HTTPS %{http_code}\\n", "https://example.com/",
			}),
			check("Linux egress guard status", status("lab-egress.service")),
			check("Recent WARP logs", logs("cloudflare-warp.service")),
			warpRestart,
		}
	case "connector":
		return []action{
			check("Check Tunnel service and edge readiness", status("lab-tunnel.service"), []string{
				"curl", "--fail", "--silent", "--show-error", "--max-time", "5", "http://127.0.0.1:20241/ready",
			}),
			check("Check Samba and IPP ports on the origin", smb, ipp),
			check("Check the Windows Server SMB port", portCheck(lab.WindowsServer, lab.Ports["smb"])),
			check("Recent Tunnel logs", logs("lab-tunnel.service")),
			restart("the Tunnel connector", "lab-tunnel.service", "Private SMB and printing will pause briefly."),
		}
	case "origin":
		return []action{
			check("Samba and printer status", status("samba-smbd.service", "lab-printer.service")),
			check("Retained print jobs, newest first", []string{"sudo", "-n", "ls", "-lht", "/var/lib/cfztna-print/"}),
			check("Recent Samba logs", logs("samba-smbd.service")),
			check("Recent printer logs", logs("lab-printer.service")),
			restart("Samba", "samba-smbd.service", "Active SMB sessions may be interrupted."),
			restart("the printer", "lab-printer.service", "Active print jobs may be interrupted."),
		}
	case "iot-gateway":
		return []action{
			check("WARP and the IoT routing table", warp, []string{"ip", "-4", "route", "show", "table", "149"}),
			check("DHCP and connected-device leases", status("dnsmasq.service"), []string{
				"sudo", "-n", "cat", "/var/lib/dnsmasq/dnsmasq.leases",
			}),
			check("Test the IoT gateway’s DNS resolver", []string{
				"dig", "@" + lab.IoT.Gateway, "example.com", "A", "+tries=1", "+time=4", "+noall", "+comments", "+answer",
			}),
			check("Isolation rules and traffic counters", []string{"sudo", "-n", "nft", "list", "table", "inet", "iot_gateway"}),
			check("Recent DHCP and DNS service logs", logs("dnsmasq.service")),
			warpRestart,
		}
	}
	return nil
}

type boundedOutput struct {
	data      bytes.Buffer
	truncated bool
}

func (b *boundedOutput) Write(p []byte) (int, error) {
	n := len(p)
	if remaining := (64 << 10) - b.data.Len(); len(p) > remaining {
		p = p[:remaining]
		b.truncated = true
	}
	_, _ = b.data.Write(p)
	return n, nil
}

type actionResult struct {
	text   string
	failed bool
}

func runAction(parent context.Context, a action) actionResult {
	var result actionResult
	var output strings.Builder
	for _, argv := range a.commands {
		if parent.Err() != nil {
			break
		}
		var commandOutput boundedOutput
		ctx, cancel := context.WithTimeout(parent, 20*time.Second)
		cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
		cmd.Stdout, cmd.Stderr = &commandOutput, &commandOutput
		err := cmd.Run()
		timedOut := ctx.Err() == context.DeadlineExceeded
		cancel()
		fmt.Fprintf(&output, "$ %s\n", strings.Join(argv, " "))
		output.WriteString(cleanOutput(commandOutput.data.String()))
		if commandOutput.truncated {
			fmt.Fprintln(&output, "\nOutput truncated after 64 KiB.")
		}
		if err != nil {
			result.failed = true
			if timedOut {
				fmt.Fprintln(&output, "\nTimed out after 20 seconds.")
			} else {
				fmt.Fprintf(&output, "\n%s: %v\n", argv[0], err)
			}
		}
		fmt.Fprintln(&output)
	}
	result.text = strings.TrimRight(output.String(), "\n")
	return result
}

func cleanOutput(s string) string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	return strings.Map(func(r rune) rune {
		if unicode.IsControl(r) && r != '\n' && r != '\t' {
			return -1
		}
		return r
	}, ansi.Strip(s))
}
