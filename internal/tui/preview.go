package tui

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/Masalale/claudetree/internal/backend"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
)

var ansiEscape = regexp.MustCompile(`\x1b\[[0-9;]*m`)

// PreviewModel is the full-screen session preview with find.
type PreviewModel struct {
	session       backend.Session
	rawMarkdown   string
	renderedLines []string
	strippedLines []string
	scrollOffset  int
	findQuery     string
	findVisible   bool
	findFocused   bool
	findInput     textinput.Model
	findMatches   []int
	findIdx       int
	caseModeIdx   int // 0=smart,1=ignore,2=match
	regexMode     bool
	width, height int
	initialQuery  string
}

func NewPreviewModel(s backend.Session, initialQuery string) PreviewModel {
	ti := textinput.New()
	ti.Placeholder = "find…"
	ti.Width = 40
	m := PreviewModel{
		session:      s,
		regexMode:    true,
		initialQuery: initialQuery,
	}
	m.findInput = ti
	if initialQuery != "" {
		m.findVisible = true
		m.findInput.SetValue(initialQuery)
		m.findInput.Focus()
		m.findQuery = initialQuery
	}
	return m
}

func (m PreviewModel) Init() tea.Cmd {
	return m.loadPreview()
}

func (m PreviewModel) loadPreview() tea.Cmd {
	sid := m.session.SID
	width := m.width
	if width <= 0 {
		width = 80
	}
	return func() tea.Msg {
		raw := backend.PreviewSession(sid)
		renderer, err := glamour.NewTermRenderer(
			glamour.WithAutoStyle(),
			glamour.WithWordWrap(width-4),
		)
		if err != nil {
			return previewLoadedMsg{content: raw, token: -1}
		}
		rendered, err := renderer.Render(raw)
		if err != nil {
			return previewLoadedMsg{content: raw, token: -1}
		}
		return previewLoadedMsg{content: rendered, token: -1}
	}
}

func (m *PreviewModel) setRendered(content string) {
	lines := strings.Split(content, "\n")
	m.renderedLines = lines
	m.strippedLines = make([]string, len(lines))
	for i, l := range lines {
		m.strippedLines[i] = ansiEscape.ReplaceAllString(l, "")
	}
	m.recomputeMatches()
}

func (m *PreviewModel) recomputeMatches() {
	if m.findQuery == "" {
		m.findMatches = nil
		m.findIdx = 0
		return
	}
	m.findMatches = computeMatches(m.strippedLines, m.findQuery, m.caseModeIdx, m.regexMode)
	if m.findIdx >= len(m.findMatches) {
		m.findIdx = 0
	}
}

func computeMatches(lines []string, query string, caseModeIdx int, regexMode bool) []int {
	if query == "" {
		return nil
	}
	var re *regexp.Regexp
	var err error

	if regexMode {
		q := query
		if caseModeIdx == 0 { // smart
			hasUpper := false
			for _, r := range query {
				if r >= 'A' && r <= 'Z' {
					hasUpper = true
					break
				}
			}
			if !hasUpper {
				q = "(?i)" + q
			}
		} else if caseModeIdx == 1 {
			q = "(?i)" + q
		}
		re, err = regexp.Compile(q)
		if err != nil {
			return nil
		}
	}

	var matches []int
	for i, line := range lines {
		var found bool
		if regexMode {
			found = re.MatchString(line)
		} else {
			haystack := line
			needle := query
			if caseModeIdx != 2 {
				haystack = strings.ToLower(haystack)
				needle = strings.ToLower(needle)
			}
			found = strings.Contains(haystack, needle)
		}
		if found {
			matches = append(matches, i)
		}
	}
	return matches
}

func (m PreviewModel) caseModeLabel() string {
	switch m.caseModeIdx {
	case 1:
		return "ignore"
	case 2:
		return "match"
	default:
		return "smart"
	}
}

func (m PreviewModel) Update(msg tea.Msg) (PreviewModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		if m.rawMarkdown != "" {
			return m, m.loadPreview()
		}
		return m, nil

	case previewLoadedMsg:
		m.rawMarkdown = msg.content
		m.setRendered(msg.content)
		if m.initialQuery != "" {
			m.recomputeMatches()
			if len(m.findMatches) > 0 {
				m.scrollToMatch(0)
			}
		}
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m PreviewModel) handleKey(msg tea.KeyMsg) (PreviewModel, tea.Cmd) {
	if m.findVisible && m.findFocused {
		return m.handleFindKey(msg)
	}

	switch msg.Type {
	case tea.KeyEnter:
		sid := m.session.SID
		return m, func() tea.Msg { return appQuitMsg{action: "resume", sid: sid} }
	case tea.KeyEsc:
		return m, func() tea.Msg { return switchScreenMsg{screen: screenBrowse} }
	case tea.KeyUp:
		if m.scrollOffset > 0 {
			m.scrollOffset--
		}
		return m, nil
	case tea.KeyDown:
		m.scrollOffset++
		return m, nil
	case tea.KeyPgUp:
		m.scrollOffset -= m.contentHeight()
		if m.scrollOffset < 0 {
			m.scrollOffset = 0
		}
		return m, nil
	case tea.KeyPgDown:
		m.scrollOffset += m.contentHeight()
		return m, nil
	case tea.KeyCtrlF:
		m.findVisible = true
		m.findFocused = true
		m.findInput.Focus()
		return m, textinput.Blink
	case tea.KeyCtrlI:
		m.caseModeIdx = (m.caseModeIdx + 1) % 3
		m.recomputeMatches()
		return m, nil
	case tea.KeyCtrlG:
		m.regexMode = !m.regexMode
		m.recomputeMatches()
		return m, nil
	}

	switch msg.String() {
	case "q":
		return m, func() tea.Msg { return appQuitMsg{} }
	case "n":
		if len(m.findMatches) > 0 {
			m.findIdx = (m.findIdx + 1) % len(m.findMatches)
			m.scrollToMatch(m.findIdx)
		}
	case "N":
		if len(m.findMatches) > 0 {
			m.findIdx = (m.findIdx - 1 + len(m.findMatches)) % len(m.findMatches)
			m.scrollToMatch(m.findIdx)
		}
	}
	return m, nil
}

func (m PreviewModel) handleFindKey(msg tea.KeyMsg) (PreviewModel, tea.Cmd) {
	switch msg.Type {
	case tea.KeyEsc:
		m.findVisible = false
		m.findFocused = false
		m.findInput.Blur()
		m.findQuery = ""
		m.findInput.SetValue("")
		m.recomputeMatches()
		return m, nil
	case tea.KeyEnter:
		m.findFocused = false
		m.findInput.Blur()
		return m, nil
	case tea.KeyCtrlI:
		m.caseModeIdx = (m.caseModeIdx + 1) % 3
		m.recomputeMatches()
		return m, nil
	case tea.KeyCtrlG:
		m.regexMode = !m.regexMode
		m.recomputeMatches()
		return m, nil
	}

	var cmd tea.Cmd
	m.findInput, cmd = m.findInput.Update(msg)
	m.findQuery = m.findInput.Value()
	m.recomputeMatches()
	if len(m.findMatches) > 0 {
		m.findIdx = 0
		m.scrollToMatch(0)
	}
	return m, cmd
}

func (m *PreviewModel) scrollToMatch(idx int) {
	if idx < 0 || idx >= len(m.findMatches) {
		return
	}
	line := m.findMatches[idx]
	offset := line - m.contentHeight()/2
	if offset < 0 {
		offset = 0
	}
	m.scrollOffset = offset
}

func (m PreviewModel) contentHeight() int {
	h := m.height - 3
	if m.findVisible {
		h--
	}
	if h < 1 {
		return 1
	}
	return h
}

func (m PreviewModel) View() string {
	label := m.session.DisplayLabel()
	meta := fmt.Sprintf("  %s  %dmsgs  %s", m.session.Age, m.session.Msgs, m.session.ProjectPath())
	title := StyleHeader.Width(m.width).Render(
		StyleBold.Render(truncate(label, 50)) + StyleDim.Render(meta),
	)

	var findBar string
	if m.findVisible {
		matchInfo := ""
		if m.findQuery != "" {
			if len(m.findMatches) == 0 {
				matchInfo = "[no matches]"
			} else {
				matchInfo = fmt.Sprintf("%d/%d %s", m.findIdx+1, len(m.findMatches), m.caseModeLabel())
				if m.regexMode {
					matchInfo += " regex"
				}
			}
		}
		findBar = StyleSubtitle.Width(m.width).Render(
			m.findInput.View() + "  " + matchInfo,
		)
	}

	ch := m.contentHeight()
	highlightSet := map[int]bool{}
	for _, idx := range m.findMatches {
		highlightSet[idx] = true
	}
	currentMatch := -1
	if m.findIdx < len(m.findMatches) {
		currentMatch = m.findMatches[m.findIdx]
	}

	var contentLines []string
	for i := m.scrollOffset; i < m.scrollOffset+ch && i < len(m.renderedLines); i++ {
		line := m.renderedLines[i]
		if highlightSet[i] {
			stripped := m.strippedLines[i]
			isCurrent := i == currentMatch
			line = highlightLine(line, stripped, m.findQuery, m.caseModeIdx, m.regexMode, isCurrent)
		}
		contentLines = append(contentLines, line)
	}
	for len(contentLines) < ch {
		contentLines = append(contentLines, "")
	}
	content := strings.Join(contentLines, "\n")
	content = lipgloss.NewStyle().Width(m.width).Height(ch).Render(content)

	footer := StyleFooter.Width(m.width).Render(
		"enter:resume  ctrl+f:find  n/N:next/prev  ctrl+i:case  ctrl+g:regex  esc:back  q:quit",
	)

	parts := []string{title}
	if m.findVisible {
		parts = append(parts, findBar)
	}
	parts = append(parts, content, footer)
	return lipgloss.JoinVertical(lipgloss.Left, parts...)
}

// highlightLine highlights matching spans in a rendered line.
func highlightLine(rendered, stripped, query string, caseModeIdx int, regexMode bool, isCurrent bool) string {
	style := StyleFindHighlight
	if isCurrent {
		style = style.Underline(true)
	}
	return style.Render(stripped)
}
