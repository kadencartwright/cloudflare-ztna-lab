package main

import (
	"context"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
)

func TestRestartRequiresConfirmation(t *testing.T) {
	m := newModel(t.Context(), "Test guest", []action{restart("printer", "printer.service", "Jobs may pause.")})
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
	m := newModel(context.Background(), "IoT gateway", []action{check("Check routes", []string{"ip"})})
	resized, _ := m.Update(tea.WindowSizeMsg{Width: 39, Height: 16})
	finished, _ := resized.Update(actionResult{text: strings.Repeat("a line of output\n", 30)})
	view := finished.View().Content
	if lipgloss.Height(view) > 16 || lipgloss.Width(view) > 39 {
		t.Fatalf("output view does not fit 39×16: got %d×%d", lipgloss.Width(view), lipgloss.Height(view))
	}
}
