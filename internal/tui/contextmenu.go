package tui

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// MenuOption is a context menu item.
type MenuOption struct {
	Label  string
	Action string
}

// ContextMenuModel is a floating context menu overlay.
type ContextMenuModel struct {
	visible bool
	options []MenuOption
	cursor  int
	x, y   int
	termW  int
	termH  int
}

func (m *ContextMenuModel) Show(options []MenuOption, x, y, termW, termH int) {
	menuWidth := 28
	menuHeight := len(options) + 2
	cx := x
	cy := y
	if cx+menuWidth > termW {
		cx = termW - menuWidth - 1
	}
	if cx < 0 {
		cx = 0
	}
	if cy+menuHeight > termH {
		cy = termH - menuHeight - 1
	}
	if cy < 0 {
		cy = 0
	}
	m.visible = true
	m.options = options
	m.cursor = 0
	m.x = cx
	m.y = cy
	m.termW = termW
	m.termH = termH
}

func (m *ContextMenuModel) Hide() {
	m.visible = false
}

func (m ContextMenuModel) Update(msg tea.Msg) (ContextMenuModel, tea.Cmd) {
	if !m.visible {
		return m, nil
	}
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyUp:
			if m.cursor > 0 {
				m.cursor--
			}
		case tea.KeyDown:
			if m.cursor < len(m.options)-1 {
				m.cursor++
			}
		case tea.KeyEnter:
			if m.cursor < len(m.options) {
				action := m.options[m.cursor].Action
				m.visible = false
				return m, func() tea.Msg { return ctxMenuDoneMsg(action) }
			}
		case tea.KeyEsc:
			m.visible = false
			return m, func() tea.Msg { return ctxMenuDoneMsg("") }
		}
		switch msg.String() {
		case "j":
			if m.cursor < len(m.options)-1 {
				m.cursor++
			}
		case "k":
			if m.cursor > 0 {
				m.cursor--
			}
		}
	case tea.MouseMsg:
		if msg.Action == tea.MouseActionPress {
			mx, my := msg.X, msg.Y
			menuWidth := 28
			menuHeight := len(m.options) + 2
			if mx < m.x || mx > m.x+menuWidth || my < m.y || my > m.y+menuHeight {
				m.visible = false
				return m, func() tea.Msg { return ctxMenuDoneMsg("") }
			}
		}
	}
	return m, nil
}

// ctxMenuDoneMsg is sent when a context menu action is chosen.
type ctxMenuDoneMsg string

// View renders the context menu as a positioned overlay string.
func (m ContextMenuModel) View(termW, termH int) string {
	if !m.visible {
		return ""
	}

	var rows []string
	for i, opt := range m.options {
		style := StyleMenuNormal
		if i == m.cursor {
			style = StyleMenuSelected
		}
		rows = append(rows, style.Width(24).Render(opt.Label))
	}
	box := StyleMenu.Width(26).Render(strings.Join(rows, "\n"))
	boxLines := strings.Split(box, "\n")

	var sb strings.Builder
	for i := 0; i < m.y; i++ {
		sb.WriteByte('\n')
	}
	leftPad := strings.Repeat(" ", m.x)
	for i, line := range boxLines {
		sb.WriteString(leftPad + line)
		if i < len(boxLines)-1 {
			sb.WriteByte('\n')
		}
	}
	return sb.String()
}

// overlayContextMenu places the context menu on top of a base view.
func overlayContextMenu(base, menu string, termW int) string {
	if menu == "" {
		return base
	}
	baseLines := strings.Split(base, "\n")
	menuLines := strings.Split(menu, "\n")

	result := make([]string, len(baseLines))
	copy(result, baseLines)

	for mi, ml := range menuLines {
		if mi >= len(result) {
			result = append(result, ml)
			continue
		}
		if ml == "" {
			continue
		}
		result[mi] = ml
	}
	return strings.Join(result, "\n")
}
