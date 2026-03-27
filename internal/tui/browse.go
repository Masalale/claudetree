package tui

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/Masalale/claudetree/internal/backend"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
)

var sortLabels = []string{"↓ Recent", "↑ Oldest", "↓ Msgs", "↑ Msgs"}

// BrowseModel is the main session browser screen.
type BrowseModel struct {
	sessions       []backend.Session
	filtered       []backend.Session
	cursor         int
	offset         int
	sortIdx        int
	filterText     string
	filterVisible  bool
	filterInput    textinput.Model
	allProjects    bool
	cwd            string
	previewContent string
	previewToken   int
	previewOffset  int
	width, height  int
	notify         string
	notifyExpire   time.Time
}

func NewBrowseModel(cwd string, allProjects bool) BrowseModel {
	ti := textinput.New()
	ti.Placeholder = "filter sessions…"
	ti.Prompt = "/ "
	m := BrowseModel{
		filterInput: ti,
		allProjects: allProjects,
		cwd:         cwd,
	}
	return m
}

func (m BrowseModel) Init() tea.Cmd {
	return func() tea.Msg { return reloadSessionsMsg{} }
}

func (m *BrowseModel) applyFilter() {
	words := strings.Fields(strings.ToLower(m.filterText))
	var filtered []backend.Session

	sorted := make([]backend.Session, len(m.sessions))
	copy(sorted, m.sessions)

	switch m.sortIdx {
	case 0:
		sort.Slice(sorted, func(i, j int) bool {
			return sorted[i].SortTime > sorted[j].SortTime
		})
	case 1:
		sort.Slice(sorted, func(i, j int) bool {
			return sorted[i].SortTime < sorted[j].SortTime
		})
	case 2:
		sort.Slice(sorted, func(i, j int) bool {
			if sorted[i].Msgs != sorted[j].Msgs {
				return sorted[i].Msgs > sorted[j].Msgs
			}
			return sorted[i].SortTime > sorted[j].SortTime
		})
	case 3:
		sort.Slice(sorted, func(i, j int) bool {
			if sorted[i].Msgs != sorted[j].Msgs {
				return sorted[i].Msgs < sorted[j].Msgs
			}
			return sorted[i].SortTime > sorted[j].SortTime
		})
	}

	for _, s := range sorted {
		if len(words) == 0 {
			filtered = append(filtered, s)
			continue
		}
		haystack := strings.ToLower(s.Name + " " + s.FirstMsg + " " + s.ProjectPath())
		match := true
		for _, w := range words {
			if !strings.Contains(haystack, w) {
				match = false
				break
			}
		}
		if match {
			filtered = append(filtered, s)
		}
	}
	m.filtered = filtered
	if m.cursor >= len(m.filtered) {
		if len(m.filtered) > 0 {
			m.cursor = len(m.filtered) - 1
		} else {
			m.cursor = 0
		}
	}
	m.clampOffset()
}

func (m *BrowseModel) clampOffset() {
	visRows := m.visibleRows()
	if visRows <= 0 {
		return
	}
	if m.cursor < m.offset {
		m.offset = m.cursor
	}
	if m.cursor >= m.offset+visRows {
		m.offset = m.cursor - visRows + 1
	}
	if m.offset < 0 {
		m.offset = 0
	}
}

func (m BrowseModel) visibleRows() int {
	used := 3
	if m.filterVisible {
		used++
	}
	v := m.height - used
	if v < 1 {
		return 1
	}
	return v
}

func (m BrowseModel) Update(msg tea.Msg) (BrowseModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.clampOffset()
		return m, m.schedulePreview()

	case reloadSessionsMsg:
		m.sessions = backend.ListSessions(m.cwd, m.allProjects)
		m.applyFilter()
		return m, m.schedulePreview()

	case previewDebounceMsg:
		if msg.token != m.previewToken {
			return m, nil
		}
		return m, loadPreviewCmd(msg.sid, msg.token)

	case previewLoadedMsg:
		if msg.token != m.previewToken {
			return m, nil
		}
		m.previewContent = msg.content
		m.previewOffset = 0
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)

	case tea.MouseMsg:
		return m.handleMouse(msg)
	}
	return m, nil
}

func (m BrowseModel) handleKey(msg tea.KeyMsg) (BrowseModel, tea.Cmd) {
	if m.filterVisible && m.filterInput.Focused() {
		return m.handleFilterKey(msg)
	}
	switch msg.Type {
	case tea.KeyUp:
		return m.moveCursor(-1)
	case tea.KeyDown:
		return m.moveCursor(1)
	case tea.KeyEnter:
		if len(m.filtered) > 0 {
			s := m.filtered[m.cursor]
			return m, func() tea.Msg {
				return switchScreenMsg{screen: screenPreview, session: &s}
			}
		}
	case tea.KeyEsc:
		if m.filterVisible {
			m.filterText = ""
			m.filterVisible = false
			m.filterInput.Blur()
			m.filterInput.SetValue("")
			m.applyFilter()
			return m, nil
		}
		return m, func() tea.Msg { return appQuitMsg{} }
	case tea.KeyCtrlD:
		return m.trashCurrent()
	case tea.KeyCtrlR:
		if len(m.filtered) > 0 {
			s := m.filtered[m.cursor]
			return m, func() tea.Msg {
				return openDialogMsg{
					kind:          dialogInput,
					prompt:        "Rename session:",
					initialValue:  s.Name,
					pendingAction: "rename",
					pendingData:   s.SID + "|||" + s.ProjectID,
				}
			}
		}
	case tea.KeyCtrlT:
		return m, func() tea.Msg { return switchScreenMsg{screen: screenTrash} }
	case tea.KeyCtrlA:
		if m.allProjects {
			return m, func() tea.Msg { return switchScreenMsg{screen: screenDirPicker} }
		}
		m.allProjects = true
		m.sessions = backend.ListSessions(m.cwd, true)
		m.applyFilter()
		return m, m.schedulePreview()
	case tea.KeyCtrlS:
		m.sortIdx = (m.sortIdx + 1) % 4
		m.applyFilter()
		return m, m.schedulePreview()
	case tea.KeyCtrlN:
		return m, func() tea.Msg { return appQuitMsg{action: "new"} }
	}

	switch msg.String() {
	case "j":
		return m.moveCursor(1)
	case "k":
		return m.moveCursor(-1)
	case "q":
		return m, func() tea.Msg { return appQuitMsg{} }
	case "/":
		m.filterVisible = true
		m.filterInput.Focus()
		return m, textinput.Blink
	case "ctrl+/", "ctrl+_":
		return m, func() tea.Msg { return switchScreenMsg{screen: screenSearch} }
	}
	return m, nil
}

func (m BrowseModel) handleFilterKey(msg tea.KeyMsg) (BrowseModel, tea.Cmd) {
	switch msg.Type {
	case tea.KeyUp:
		return m.moveCursor(-1)
	case tea.KeyDown:
		return m.moveCursor(1)
	case tea.KeyEnter:
		m.filterVisible = false
		m.filterInput.Blur()
		if len(m.filtered) > 0 {
			s := m.filtered[m.cursor]
			return m, func() tea.Msg {
				return switchScreenMsg{screen: screenPreview, session: &s}
			}
		}
		return m, nil
	case tea.KeyEsc:
		m.filterText = ""
		m.filterVisible = false
		m.filterInput.Blur()
		m.filterInput.SetValue("")
		m.applyFilter()
		return m, nil
	case tea.KeyCtrlD:
		return m.trashCurrent()
	case tea.KeyCtrlT:
		return m, func() tea.Msg { return switchScreenMsg{screen: screenTrash} }
	case tea.KeyCtrlN:
		return m, func() tea.Msg { return appQuitMsg{action: "new"} }
	}

	var cmd tea.Cmd
	m.filterInput, cmd = m.filterInput.Update(msg)
	m.filterText = m.filterInput.Value()
	m.applyFilter()
	return m, cmd
}

func (m BrowseModel) handleMouse(msg tea.MouseMsg) (BrowseModel, tea.Cmd) {
	if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonWheelUp {
		return m.moveCursor(-1)
	}
	if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonWheelDown {
		return m.moveCursor(1)
	}
	if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonRight {
		if len(m.filtered) > 0 {
			s := m.filtered[m.cursor]
			options := []MenuOption{
				{Label: "Resume", Action: "resume:" + s.SID},
				{Label: "Rename", Action: "rename:" + s.SID},
				{Label: "Trash", Action: "trash:" + s.SID},
				{Label: "New session", Action: "new"},
			}
			return m, func() tea.Msg {
				return ctxMenuShowMsg{options: options, x: msg.X, y: msg.Y}
			}
		}
	}
	return m, nil
}

func (m BrowseModel) trashCurrent() (BrowseModel, tea.Cmd) {
	if len(m.filtered) == 0 {
		return m, nil
	}
	s := m.filtered[m.cursor]
	sid := s.SID
	return m, func() tea.Msg {
		if err := backend.TrashSession(sid); err != nil {
			return notifyMsg{text: "Error: " + err.Error()}
		}
		return reloadSessionsMsg{}
	}
}

func (m BrowseModel) moveCursor(delta int) (BrowseModel, tea.Cmd) {
	if len(m.filtered) == 0 {
		return m, nil
	}
	m.cursor += delta
	if m.cursor < 0 {
		m.cursor = 0
	}
	if m.cursor >= len(m.filtered) {
		m.cursor = len(m.filtered) - 1
	}
	m.clampOffset()
	return m, m.schedulePreview()
}

func (m BrowseModel) schedulePreview() tea.Cmd {
	if len(m.filtered) == 0 {
		return nil
	}
	m.previewToken++
	token := m.previewToken
	sid := m.filtered[m.cursor].SID
	return tea.Tick(80*time.Millisecond, func(t time.Time) tea.Msg {
		return previewDebounceMsg{token: token, sid: sid}
	})
}

func (m BrowseModel) View() string {
	leftW := m.width * 45 / 100
	if leftW < 20 {
		leftW = 20
	}
	rightW := m.width - leftW - 1
	if rightW < 1 {
		rightW = 1
	}

	title := StyleHeader.Width(m.width).Render("claudetree")
	scope := m.cwd
	if m.allProjects {
		scope = "all projects"
	}
	subtitle := StyleSubtitle.Width(m.width).Render(
		fmt.Sprintf("  %s  %s", scope, sortLabels[m.sortIdx]),
	)

	visRows := m.visibleRows()
	var listLines []string
	for i := m.offset; i < m.offset+visRows && i < len(m.filtered); i++ {
		listLines = append(listLines, m.renderItem(m.filtered[i], i == m.cursor, leftW-2))
	}
	for len(listLines) < visRows {
		listLines = append(listLines, strings.Repeat(" ", leftW-2))
	}

	if m.filterVisible {
		filterBar := m.filterInput.View()
		listLines = append(listLines, StyleDim.Render(filterBar))
	}

	leftContent := strings.Join(listLines, "\n")
	bodyHeight := m.height - 3
	if bodyHeight < 1 {
		bodyHeight = 1
	}
	leftPane := StylePane.Width(leftW).Height(bodyHeight).Render(leftContent)

	previewLines := strings.Split(m.previewContent, "\n")
	end := m.previewOffset + bodyHeight
	if end > len(previewLines) {
		end = len(previewLines)
	}
	visible := previewLines
	if m.previewOffset < len(previewLines) {
		visible = previewLines[m.previewOffset:end]
	}
	rightContent := strings.Join(visible, "\n")
	rightPane := lipgloss.NewStyle().Width(rightW).Height(bodyHeight).Render(rightContent)

	body := lipgloss.JoinHorizontal(lipgloss.Top, leftPane, rightPane)

	footer := StyleFooter.Width(m.width).Render(
		"enter:preview  ctrl+d:trash  ctrl+r:rename  ctrl+t:trash-bin  ctrl+a:dir  ctrl+s:sort  ctrl+n:new  q:quit",
	)

	return lipgloss.JoinVertical(lipgloss.Left, title, subtitle, body, footer)
}

func (m BrowseModel) renderItem(s backend.Session, selected bool, width int) string {
	meta := StyleDim.Render(fmt.Sprintf("%4s  %3dm", s.Age, s.Msgs))
	var label string
	if s.Name != "" {
		nameStr := StyleCyan.Render(truncate(s.Name, 30))
		firstStr := StyleDim.Render(truncate(s.FirstMsg, 25))
		label = meta + "  " + nameStr + "  " + firstStr
	} else {
		label = meta + "  " + truncate(s.FirstMsg, 50)
	}
	if m.allProjects {
		label += "  " + StyleDim.Render(s.ProjectPath())
	}

	if selected {
		return StyleSelect.Width(width).Render(label)
	}
	return lipgloss.NewStyle().Width(width).Render(label)
}

func truncate(s string, n int) string {
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	return string(runes[:n-1]) + "…"
}

func loadPreviewCmd(sid string, token int) tea.Cmd {
	return func() tea.Msg {
		raw := backend.PreviewSession(sid)
		renderer, err := glamour.NewTermRenderer(
			glamour.WithAutoStyle(),
			glamour.WithWordWrap(80),
		)
		if err != nil {
			return previewLoadedMsg{content: raw, token: token}
		}
		rendered, err := renderer.Render(raw)
		if err != nil {
			return previewLoadedMsg{content: raw, token: token}
		}
		return previewLoadedMsg{content: rendered, token: token}
	}
}
