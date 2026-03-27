package tui

import (
	"fmt"
	"strings"
	"time"

	"github.com/Masalale/claudetree/internal/backend"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
)

// SearchModel is the content search screen.
type SearchModel struct {
	query          string
	searchInput    textinput.Model
	results        []backend.Session
	cursor         int
	offset         int
	searchFocused  bool
	caseModeIdx    int
	regexMode      bool
	previewContent string
	previewToken   int
	cwd            string
	allProjects    bool
	width, height  int
}

func NewSearchModel(cwd string, allProjects bool) SearchModel {
	ti := textinput.New()
	ti.Placeholder = "search session content with ripgrep…"
	ti.Focus()
	return SearchModel{
		searchInput:   ti,
		searchFocused: true,
		cwd:           cwd,
		allProjects:   allProjects,
		regexMode:     true,
	}
}

func (m SearchModel) Init() tea.Cmd { return textinput.Blink }

func (m SearchModel) caseModeLabel() string {
	switch m.caseModeIdx {
	case 1:
		return "ignore"
	case 2:
		return "match"
	default:
		return "smart"
	}
}

func (m SearchModel) Update(msg tea.Msg) (SearchModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case searchResultsMsg:
		m.results = msg.results
		m.cursor = 0
		m.offset = 0
		return m, m.schedulePreview()

	case previewDebounceMsg:
		if msg.token != m.previewToken {
			return m, nil
		}
		return m, searchLoadPreviewCmd(msg.sid, msg.token)

	case previewLoadedMsg:
		if msg.token != m.previewToken {
			return m, nil
		}
		m.previewContent = msg.content
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m SearchModel) handleKey(msg tea.KeyMsg) (SearchModel, tea.Cmd) {
	if m.searchFocused {
		return m.handleSearchKey(msg)
	}
	return m.handleListKey(msg)
}

func (m SearchModel) handleSearchKey(msg tea.KeyMsg) (SearchModel, tea.Cmd) {
	switch msg.Type {
	case tea.KeyEnter:
		m.query = m.searchInput.Value()
		if m.query == "" {
			return m, nil
		}
		q := m.query
		cwd := m.cwd
		ap := m.allProjects
		useRegex := m.regexMode
		cm := m.caseModeLabel()
		m.searchFocused = false
		m.searchInput.Blur()
		return m, func() tea.Msg {
			results := backend.SearchSessions(q, cwd, ap, useRegex, cm)
			return searchResultsMsg{results: results}
		}
	case tea.KeyUp:
		return m.moveResultCursor(-1)
	case tea.KeyDown:
		return m.moveResultCursor(1)
	case tea.KeyEsc:
		return m, func() tea.Msg { return switchScreenMsg{screen: screenBrowse} }
	case tea.KeyCtrlG:
		m.regexMode = !m.regexMode
		return m, nil
	case tea.KeyCtrlI:
		m.caseModeIdx = (m.caseModeIdx + 1) % 3
		return m, nil
	}
	var cmd tea.Cmd
	m.searchInput, cmd = m.searchInput.Update(msg)
	return m, cmd
}

func (m SearchModel) handleListKey(msg tea.KeyMsg) (SearchModel, tea.Cmd) {
	switch msg.Type {
	case tea.KeyUp:
		return m.moveResultCursor(-1)
	case tea.KeyDown:
		return m.moveResultCursor(1)
	case tea.KeyEnter:
		if len(m.results) > 0 {
			s := m.results[m.cursor]
			q := m.query
			return m, func() tea.Msg {
				return switchScreenMsg{screen: screenPreview, session: &s, initialQuery: q}
			}
		}
	case tea.KeyEsc:
		return m, func() tea.Msg { return switchScreenMsg{screen: screenBrowse} }
	case tea.KeyCtrlD:
		if len(m.results) > 0 {
			sid := m.results[m.cursor].SID
			q := m.query
			cwd := m.cwd
			ap := m.allProjects
			useRegex := m.regexMode
			cm := m.caseModeLabel()
			return m, func() tea.Msg {
				backend.TrashSession(sid)
				results := backend.SearchSessions(q, cwd, ap, useRegex, cm)
				return searchResultsMsg{results: results}
			}
		}
	case tea.KeyCtrlG:
		m.regexMode = !m.regexMode
		return m, nil
	case tea.KeyCtrlI:
		m.caseModeIdx = (m.caseModeIdx + 1) % 3
		return m, nil
	}
	switch msg.String() {
	case "ctrl+/":
		m.searchFocused = true
		m.searchInput.Focus()
		return m, textinput.Blink
	case "ctrl+b":
		return m, func() tea.Msg { return switchScreenMsg{screen: screenBrowse} }
	case "j":
		return m.moveResultCursor(1)
	case "k":
		return m.moveResultCursor(-1)
	case "q":
		return m, func() tea.Msg { return appQuitMsg{} }
	}
	return m, nil
}

func (m SearchModel) moveResultCursor(delta int) (SearchModel, tea.Cmd) {
	if len(m.results) == 0 {
		return m, nil
	}
	m.cursor += delta
	if m.cursor < 0 {
		m.cursor = 0
	}
	if m.cursor >= len(m.results) {
		m.cursor = len(m.results) - 1
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

func (m SearchModel) visibleRows() int {
	v := m.height - 4
	if v < 1 {
		return 1
	}
	return v
}

func (m SearchModel) schedulePreview() tea.Cmd {
	if len(m.results) == 0 {
		return nil
	}
	m.previewToken++
	token := m.previewToken
	sid := m.results[m.cursor].SID
	return tea.Tick(80*time.Millisecond, func(_ time.Time) tea.Msg {
		return previewDebounceMsg{token: token, sid: sid}
	})
}

func searchLoadPreviewCmd(sid string, token int) tea.Cmd {
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

func (m SearchModel) View() string {
	leftW := m.width * 45 / 100
	if leftW < 20 {
		leftW = 20
	}
	rightW := m.width - leftW - 1
	if rightW < 1 {
		rightW = 1
	}

	modeStr := m.caseModeLabel()
	if m.regexMode {
		modeStr += " regex"
	} else {
		modeStr += " literal"
	}
	subtitle := ""
	if m.query != "" {
		subtitle = fmt.Sprintf("search: %s (%s)", m.query, modeStr)
	}
	title := StyleHeader.Width(m.width).Render("claudetree  —  content search")
	sub := StyleSubtitle.Width(m.width).Render("  " + subtitle)

	searchBar := StyleSubtitle.Width(leftW).Render(m.searchInput.View())

	visRows := m.visibleRows() - 1
	if visRows < 0 {
		visRows = 0
	}
	var listLines []string
	for i := m.offset; i < m.offset+visRows && i < len(m.results); i++ {
		s := m.results[i]
		meta := StyleDim.Render(fmt.Sprintf("%4s  %3dm", s.Age, s.Msgs))
		var label string
		if s.Name != "" {
			label = meta + "  " + StyleCyan.Render(truncate(s.Name, 30))
		} else {
			label = meta + "  " + truncate(s.FirstMsg, 40)
		}
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
	leftContent := searchBar + "\n" + strings.Join(listLines, "\n")
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
		"enter:search/resume  ctrl+d:trash  ctrl+/:refocus  ctrl+b:back  q:quit",
	)

	return lipgloss.JoinVertical(lipgloss.Left, title, sub, body, footer)
}
