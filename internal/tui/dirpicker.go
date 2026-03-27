package tui

import (
	"fmt"
	"sort"
	"strings"

	"github.com/Masalale/claudetree/internal/backend"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

type dirEntry struct {
	Path  string
	Count int
}

// DirPickerModel lists project directories for selection.
type DirPickerModel struct {
	allDirs       []dirEntry
	filtered      []dirEntry
	cursor        int
	filter        textinput.Model
	width, height int
}

func NewDirPickerModel() DirPickerModel {
	ti := textinput.New()
	ti.Placeholder = "filter directories…"
	ti.Focus()
	m := DirPickerModel{filter: ti}
	return m
}

func (m DirPickerModel) Init() tea.Cmd {
	return func() tea.Msg {
		sessions := backend.ListSessions("", true)
		counts := map[string]int{}
		for _, s := range sessions {
			counts[s.ProjectPath()]++
		}
		var dirs []dirEntry
		for path, count := range counts {
			dirs = append(dirs, dirEntry{Path: path, Count: count})
		}
		sort.Slice(dirs, func(i, j int) bool {
			return dirs[i].Path < dirs[j].Path
		})
		return dirPickerLoadedMsg{dirs: dirs}
	}
}

type dirPickerLoadedMsg struct{ dirs []dirEntry }

func (m DirPickerModel) Update(msg tea.Msg) (DirPickerModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case dirPickerLoadedMsg:
		m.allDirs = msg.dirs
		m.applyFilter()
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m *DirPickerModel) applyFilter() {
	q := strings.ToLower(m.filter.Value())
	var filtered []dirEntry
	for _, d := range m.allDirs {
		if q == "" || strings.Contains(strings.ToLower(d.Path), q) {
			filtered = append(filtered, d)
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
}

func (m DirPickerModel) handleKey(msg tea.KeyMsg) (DirPickerModel, tea.Cmd) {
	switch msg.Type {
	case tea.KeyUp:
		if m.cursor > 0 {
			m.cursor--
		}
		return m, nil
	case tea.KeyDown:
		if m.cursor < len(m.filtered)-1 {
			m.cursor++
		}
		return m, nil
	case tea.KeyEnter:
		if m.cursor < len(m.filtered) {
			cwd := m.filtered[m.cursor].Path
			return m, func() tea.Msg {
				return switchScreenMsg{screen: screenBrowse, cwd: cwd, allProjects: false}
			}
		}
	case tea.KeyEsc:
		return m, func() tea.Msg {
			return switchScreenMsg{screen: screenBrowse, allProjects: true}
		}
	}

	switch msg.String() {
	case "j":
		if m.cursor < len(m.filtered)-1 {
			m.cursor++
		}
	case "k":
		if m.cursor > 0 {
			m.cursor--
		}
	}

	var cmd tea.Cmd
	m.filter, cmd = m.filter.Update(msg)
	m.applyFilter()
	return m, cmd
}

func (m DirPickerModel) View() string {
	title := StyleHeader.Width(m.width).Render("claudetree  —  select project")
	filterBar := StyleSubtitle.Width(m.width).Render(m.filter.View())

	visRows := m.height - 3
	if visRows < 1 {
		visRows = 1
	}
	var lines []string
	for i := 0; i < visRows && i < len(m.filtered); i++ {
		d := m.filtered[i]
		item := fmt.Sprintf("%-50s  %s", d.Path, StyleDim.Render(fmt.Sprintf("%d sessions", d.Count)))
		if i == m.cursor {
			lines = append(lines, StyleSelect.Width(m.width).Render(item))
		} else {
			lines = append(lines, lipgloss.NewStyle().Width(m.width).Render(item))
		}
	}
	for len(lines) < visRows {
		lines = append(lines, "")
	}

	footer := StyleFooter.Width(m.width).Render("enter:select  esc:all projects  q:quit")
	return lipgloss.JoinVertical(lipgloss.Left, title, filterBar, strings.Join(lines, "\n"), footer)
}
