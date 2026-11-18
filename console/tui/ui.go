package main

import (
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"

	"charm.land/bubbles/v2/spinner"
	"charm.land/bubbles/v2/viewport"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

type screen int

const (
	menuScreen screen = iota
	confirmScreen
	busyScreen
	resultScreen
)

const resultHelp = "↑/↓ or PgUp/PgDn Scroll   Enter / Esc Back   Q Disconnect"

var banners = map[string]string{
	"origin": `+------------+
|[====] o o  |
|[====] o o  |
|[====] o o  |
+------------+`,
	"iot-gateway": `    .----.
   /      \
  |        |
+------------+
|     ()     |
|     ||     |
+------------+`,
	"connector": `      .--.
  .--'    '--.
 (            ).
  '------------'`,
}

var (
	brandStyle     = lipgloss.NewStyle().Foreground(lipgloss.Color("208")).Bold(true)
	titleStyle     = lipgloss.NewStyle().Bold(true)
	selectedStyle  = lipgloss.NewStyle().Background(lipgloss.Color("24")).Foreground(lipgloss.Color("231")).Bold(true)
	footerStyle    = lipgloss.NewStyle().Foreground(lipgloss.Color("245"))
	successStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("78")).Bold(true)
	attentionStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("221")).Bold(true)
)

type shellResult struct{ err error }

type model struct {
	ctx      context.Context
	title    string
	banner   string
	actions  []action
	selected int
	screen   screen
	width    int
	height   int
	output   viewport.Model
	spinner  spinner.Model
	result   actionResult
	shellErr error
}

func newModel(ctx context.Context, role string, actions []action) model {
	progress := spinner.New(spinner.WithSpinner(spinner.Dot))
	progress.Style = brandStyle
	output := viewport.New(viewport.WithWidth(76), viewport.WithHeight(14))
	output.SoftWrap = true
	return model{ctx: ctx, title: titles[role], banner: banners[role], actions: actions, width: 80, height: 24, output: output, spinner: progress}
}

func (m model) Init() tea.Cmd { return nil }

func (m model) innerWidth() int { return max(1, min(m.width-4, 88)) }

func (m model) runSelected() (tea.Model, tea.Cmd) {
	m.screen = busyScreen
	m.result = actionResult{}
	selected := m.actions[m.selected]
	return m, tea.Batch(m.spinner.Tick, func() tea.Msg { return runAction(m.ctx, selected) })
}

func (m model) choose() (tea.Model, tea.Cmd) {
	if m.actions[m.selected].impact != "" {
		m.screen = confirmScreen
		return m, nil
	}
	return m.runSelected()
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.output.SetWidth(m.innerWidth())
		helpHeight := lipgloss.Height(ansi.Wrap(resultHelp, m.innerWidth(), ""))
		m.output.SetHeight(max(1, m.height-9-helpHeight))
	case actionResult:
		m.result, m.screen = msg, resultScreen
		m.output.SetContent(msg.text)
		m.output.GotoTop()
	case shellResult:
		m.shellErr = msg.err
	case spinner.TickMsg:
		if m.screen == busyScreen {
			var cmd tea.Cmd
			m.spinner, cmd = m.spinner.Update(msg)
			return m, cmd
		}
	case tea.KeyPressMsg:
		key := strings.ToLower(msg.String())
		if key == "ctrl+c" || (key == "q" && m.screen != busyScreen) {
			return m, tea.Quit
		}
		switch m.screen {
		case menuScreen:
			m.shellErr = nil
			switch key {
			case "up", "k":
				m.selected = max(0, m.selected-1)
			case "down", "j":
				m.selected = min(len(m.actions)-1, m.selected+1)
			case "enter":
				return m.choose()
			case "s":
				return m, tea.ExecProcess(exec.Command("bash", "-l"), func(err error) tea.Msg { return shellResult{err} })
			default:
				if number, err := strconv.Atoi(key); err == nil && number >= 1 && number <= len(m.actions) {
					m.selected = number - 1
					return m.choose()
				}
			}
		case confirmScreen:
			switch key {
			case "y":
				return m.runSelected()
			case "esc", "n", "enter":
				m.screen = menuScreen
			}
		case resultScreen:
			if key == "esc" || key == "enter" {
				m.screen = menuScreen
			} else {
				var cmd tea.Cmd
				m.output, cmd = m.output.Update(msg)
				return m, cmd
			}
		}
	}
	return m, nil
}

func (m model) View() tea.View {
	width := m.innerWidth()
	trim := func(s string) string { return ansi.Truncate(s, width, "…") }
	rows := []string{
		brandStyle.Render(trim("Cloudflare Zero Trust Lab")),
		titleStyle.Render(trim(m.title)),
		"",
	}
	var footer string
	switch m.screen {
	case menuScreen:
		footer = fmt.Sprintf("↑/↓ Select   Enter Run   1–%d Shortcut   S Shell   Q Disconnect", len(m.actions))
		visible := max(1, m.height-6-lipgloss.Height(ansi.Wrap(footer, width, "")))
		if m.shellErr != nil {
			visible = max(1, visible-2)
		}
		bannerRows := lipgloss.Height(m.banner) + 1
		if m.banner != "" && lipgloss.Width(m.banner) <= width && visible-bannerRows >= len(m.actions) {
			rows = append(rows, brandStyle.Render(m.banner), "")
			visible -= bannerRows
		}
		start := max(0, m.selected-visible+1)
		for index := start; index < min(len(m.actions), start+visible); index++ {
			label := fmt.Sprintf("%d  %s", index+1, m.actions[index].label)
			if index == m.selected {
				rows = append(rows, selectedStyle.Render(trim("›  "+label)))
			} else {
				rows = append(rows, trim("   "+label))
			}
		}
		if m.shellErr != nil {
			rows = append(rows, "", attentionStyle.Render(trim("Shell exited: "+m.shellErr.Error())))
		}
	case confirmScreen:
		rows = append(rows, attentionStyle.Render(trim(m.actions[m.selected].label)), "",
			ansi.Wrap(m.actions[m.selected].impact, width, ""))
		footer = "Y Confirm restart   Enter / Esc Cancel"
	case busyScreen:
		rows = append(rows, m.spinner.View()+" "+trim(m.actions[m.selected].label))
	case resultScreen:
		summary := successStyle.Render("Complete")
		if m.result.failed {
			summary = attentionStyle.Render("Check failed")
		}
		rows = append(rows, titleStyle.Render(trim(m.actions[m.selected].label)), summary, "", m.output.View())
		footer = resultHelp
	}
	if footer != "" {
		rows = append(rows, "", footerStyle.Render(ansi.Wrap(footer, width, "")))
	}
	view := tea.NewView(lipgloss.NewStyle().Padding(1, 2).Render(strings.Join(rows, "\n")))
	view.AltScreen = true
	return view
}
