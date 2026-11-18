package main

import (
	"context"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
)

func TestRestartRequiresConfirmation(t *testing.T) {
	m := newModel(t.Context(), "origin", []action{restart("printer", "printer.service", "Jobs may pause.")})
	selected, cmd := m.Update(tea.KeyPressMsg{Code: '1', Text: "1"})
	if selected.(model).screen != confirmScreen || cmd != nil {
		t.Fatal("a restart shortcut must open confirmation")
	}
	cancelled, cmd := selected.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	if cancelled.(model).screen != menuScreen || cmd != nil {
		t.Fatal("Enter must cancel a pending restart")
	}
	confirmed, cmd := selected.Update(tea.KeyPressMsg{Code: 'Y', Text: "Y"})
	if confirmed.(model).screen != busyScreen || cmd == nil {
		t.Fatal("explicit confirmation must queue a restart")
	}
}

func TestOutputFitsNarrowTerminal(t *testing.T) {
	m := newModel(context.Background(), "iot-gateway", []action{check("Check routes", []string{"ip"})})
	resized, _ := m.Update(tea.WindowSizeMsg{Width: 39, Height: 16})
	finished, _ := resized.Update(actionResult{text: strings.Repeat("a line of output\n", 30)})
	view := finished.View().Content
	if lipgloss.Height(view) > 16 || lipgloss.Width(view) > 39 {
		t.Fatalf("output view does not fit 39×16: got %d×%d", lipgloss.Width(view), lipgloss.Height(view))
	}
}

func TestRoleBannerKeepsActionsVisible(t *testing.T) {
	for role, art := range map[string]string{"origin": "[====] o o", "iot-gateway": "|     ()", "connector": ".--'    '--."} {
		t.Run(role, func(t *testing.T) {
			m := newModel(t.Context(), role, []action{check("Test action", []string{"true"})})
			if view := m.View().Content; !strings.Contains(view, art) || !strings.Contains(view, "Test action") {
				t.Fatal("the full menu must show the role's art and action")
			}
			resized, _ := m.Update(tea.WindowSizeMsg{Width: 39, Height: 10})
			if view := resized.View().Content; strings.Contains(view, art) || !strings.Contains(view, "Test action") {
				t.Fatal("a short terminal must prioritize actions over the banner")
			}
		})
	}
}
