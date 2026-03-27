package tui

import (
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// InputDialogModel is a centered text input overlay.
type InputDialogModel struct {
	prompt string
	input  textinput.Model
	width  int
}

func NewInputDialog(prompt, initialValue string) InputDialogModel {
	ti := textinput.New()
	ti.SetValue(initialValue)
	ti.Focus()
	ti.Width = 56
	return InputDialogModel{
		prompt: prompt,
		input:  ti,
		width:  62,
	}
}

func (m InputDialogModel) Update(msg tea.Msg) (InputDialogModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyEnter, tea.KeyEsc:
			return m, nil // handled by parent
		}
	}
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	return m, cmd
}

func (m InputDialogModel) View(termW, termH int) string {
	content := lipgloss.JoinVertical(lipgloss.Left,
		StyleBold.Render(m.prompt),
		"",
		m.input.View(),
		"",
		StyleDim.Render("Enter to confirm • Esc to cancel"),
	)
	box := StyleDialog.Width(m.width).Render(content)
	return centerOverlay(box, termW, termH)
}

// ConfirmDialogModel is a centered yes/no confirmation overlay.
type ConfirmDialogModel struct {
	message string
	input   textinput.Model
}

func NewConfirmDialog(message string) ConfirmDialogModel {
	ti := textinput.New()
	ti.Placeholder = "type y + Enter to confirm, Escape cancels"
	ti.Focus()
	ti.Width = 46
	return ConfirmDialogModel{
		message: message,
		input:   ti,
	}
}

func (m ConfirmDialogModel) Update(msg tea.Msg) (ConfirmDialogModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyEnter, tea.KeyEsc:
			return m, nil // handled by parent
		}
	}
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	return m, cmd
}

func (m ConfirmDialogModel) View(termW, termH int) string {
	content := lipgloss.JoinVertical(lipgloss.Left,
		StyleBold.Render(m.message),
		"",
		m.input.View(),
	)
	box := StyleConfirm.Width(56).Render(content)
	return centerOverlay(box, termW, termH)
}

// centerOverlay renders content centered over terminal.
func centerOverlay(content string, termW, termH int) string {
	lines := strings.Split(content, "\n")
	h := len(lines)
	w := lipgloss.Width(content)

	topPad := (termH - h) / 2
	leftPad := (termW - w) / 2
	if topPad < 0 {
		topPad = 0
	}
	if leftPad < 0 {
		leftPad = 0
	}

	padLine := strings.Repeat(" ", leftPad)
	var sb strings.Builder
	for i := 0; i < topPad; i++ {
		sb.WriteByte('\n')
	}
	for i, line := range lines {
		sb.WriteString(padLine + line)
		if i < len(lines)-1 {
			sb.WriteByte('\n')
		}
	}
	return sb.String()
}
