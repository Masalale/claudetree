package tui

import (
	"fmt"
	"strings"
	"time"

	"github.com/Masalale/claudetree/internal/backend"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
)

// TrashModel is the trash bin screen.
type TrashModel struct {
	entries        []backend.TrashEntry
	cursor         int
	offset         int
	previewContent string
	previewToken   int
	width, height  int
}

func NewTrashModel() TrashModel {
	return TrashModel{}
}

func (m TrashModel) Init() tea.Cmd {
	return func() tea.Msg { return reloadTrashMsg{} }
}

func (m TrashModel) Update(msg tea.Msg) (TrashModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case reloadTrashMsg:
		m.entries = backend.ListTrash()
		if m.cursor >= len(m.entries) {
			if len(m.entries) > 0 {
				m.cursor = len(m.entries) - 1
			} else {
				m.cursor = 0
			}
		}
		return m, m.schedulePreview()

	case previewDebounceMsg:
		if msg.token != m.previewToken {
			return m, nil
		}
		return m, trashLoadPreviewCmd(msg.sid, msg.token)

	case previewLoadedMsg:
		if msg.token != m.previewToken {
			return m, nil
		}
		m.previewContent = msg.content
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)

	case tea.MouseMsg:
		return m.handleMouse(msg)
	}
	return m, nil
}

func (m TrashModel) handleKey(msg tea.KeyMsg) (TrashModel, tea.Cmd) {
	switch msg.Type {
	case tea.KeyUp:
		return m.moveCursor(-1)
	case tea.KeyDown:
		return m.moveCursor(1)
	case tea.KeyEnter:
		if len(m.entries) > 0 {
			sid := m.entries[m.cursor].SID
			return m, func() tea.Msg {
				backend.RestoreSession(sid)
				return reloadTrashMsg{}
			}
		}
	case tea.KeyCtrlD:
		if len(m.entries) > 0 {
			sid := m.entries[m.cursor].SID
			sidDisplay := sid
			if len(sidDisplay) > 24 {
				sidDisplay = sidDisplay[:24]
			}
			return m, func() tea.Msg {
				return openDialogMsg{
					kind:          dialogConfirm,
					prompt:        fmt.Sprintf("Delete %s forever?", sidDisplay),
					pendingAction: "delete_forever",
					pendingData:   sid,
				}
			}
		}
	case tea.KeyCtrlE:
		return m, func() tea.Msg {
			return openDialogMsg{
				kind:          dialogConfirm,
				prompt:        "Empty all trash?",
				pendingAction: "empty_trash",
			}
		}
	case tea.KeyCtrlB, tea.KeyEsc:
		return m, func() tea.Msg { return switchScreenMsg{screen: screenBrowse} }
	}
	switch msg.String() {
	case "j":
		return m.moveCursor(1)
	case "k":
		return m.moveCursor(-1)
	case "q":
		return m, func() tea.Msg { return appQuitMsg{} }
	}
	return m, nil
}

func (m TrashModel) handleMouse(msg tea.MouseMsg) (TrashModel, tea.Cmd) {
	if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonRight {
		if len(m.entries) > 0 {
			e := m.entries[m.cursor]
			options := []MenuOption{
				{Label: "Restore", Action: "restore:" + e.SID},
				{Label: "Delete forever", Action: "delete_forever:" + e.SID},
				{Label: "Empty trash", Action: "empty_trash"},
			}
			return m, func() tea.Msg {
				return ctxMenuShowMsg{options: options, x: msg.X, y: msg.Y}
			}
		}
	}
	return m, nil
}

func (m TrashModel) moveCursor(delta int) (TrashModel, tea.Cmd) {
	if len(m.entries) == 0 {
		return m, nil
	}
	m.cursor += delta
	if m.cursor < 0 {
		m.cursor = 0
	}
	if m.cursor >= len(m.entries) {
		m.cursor = len(m.entries) - 1
	}
	visRows := m.visibleRows()
	if m.cursor < m.offset {
		m.offset = m.cursor
	}
	if m.cursor >= m.offset+visRows {
		m.offset = m.cursor - visRows + 1
	}
	return m, m.schedulePreview()
}

func (m TrashModel) visibleRows() int {
	v := m.height - 3
	if v < 1 {
		return 1
	}
	return v
}

func (m TrashModel) schedulePreview() tea.Cmd {
	if len(m.entries) == 0 {
		return nil
	}
	m.previewToken++
	token := m.previewToken
	sid := m.entries[m.cursor].SID
	return tea.Tick(80*time.Millisecond, func(_ time.Time) tea.Msg {
		return previewDebounceMsg{token: token, sid: sid}
	})
}

func trashLoadPreviewCmd(sid string, token int) tea.Cmd {
	return func() tea.Msg {
		raw := backend.PreviewSession(sid)
		renderer, err := glamour.NewTermRenderer(
			glamour.WithAutoStyle(),
			glamour.WithWordWrap(80),
		)
		if err != nil {
			return previewLoadedMsg{content: raw, token: token}
		}
		rendered, _ := renderer.Render(raw)
		return previewLoadedMsg{content: rendered, token: token}
	}
}

func (m TrashModel) View() string {
	leftW := m.width * 45 / 100
	if leftW < 20 {
		leftW = 20
	}
	rightW := m.width - leftW - 1
	if rightW < 1 {
		rightW = 1
	}

	title := StyleHeader.Width(m.width).Render("claudetree  —  trash bin")
	subtitle := StyleSubtitle.Width(m.width).Render(fmt.Sprintf("  %d items", len(m.entries)))

	visRows := m.visibleRows()
	var listLines []string
	for i := m.offset; i < m.offset+visRows && i < len(m.entries); i++ {
		e := m.entries[i]
		when := StyleDim.Render(fmt.Sprintf("%8s", e.When))
		name := e.Name
		if name == "" {
			name = e.SID
			if len(name) > 24 {
				name = name[:24]
			}
		}
		label := when + "  " + StyleRed.Render(truncate(name, 30)) + "  " + StyleDim.Render(e.ProjectPath())
		if i == m.cursor {
			listLines = append(listLines, StyleSelect.Width(leftW-2).Render(label))
		} else {
			listLines = append(listLines, lipgloss.NewStyle().Width(leftW-2).Render(label))
		}
	}
	for len(listLines) < visRows {
		listLines = append(listLines, strings.Repeat(" ", leftW-2))
	}

	bodyHeight := m.height - 3
	if bodyHeight < 1 {
		bodyHeight = 1
	}
	leftContent := strings.Join(listLines, "\n")
	leftPane := StylePane.Width(leftW).Height(bodyHeight).Render(leftContent)

	previewLines := strings.Split(m.previewContent, "\n")
	ch := bodyHeight
	if ch > len(previewLines) {
		ch = len(previewLines)
	}
	rightContent := strings.Join(previewLines[:ch], "\n")
	rightPane := lipgloss.NewStyle().Width(rightW).Height(bodyHeight).Render(rightContent)

	body := lipgloss.JoinHorizontal(lipgloss.Top, leftPane, rightPane)
	footer := StyleFooter.Width(m.width).Render(
		"enter:restore  ctrl+d:delete  ctrl+e:empty  ctrl+b:back  q:quit",
	)

	return lipgloss.JoinVertical(lipgloss.Left, title, subtitle, body, footer)
}
