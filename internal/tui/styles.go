package tui

import "github.com/charmbracelet/lipgloss"

var (
	ColorPrimary   = lipgloss.AdaptiveColor{Light: "#5c7cfa", Dark: "#7c8cf8"}
	ColorMuted     = lipgloss.AdaptiveColor{Light: "#555555", Dark: "#777777"}
	ColorWarning   = lipgloss.AdaptiveColor{Light: "#e07000", Dark: "#f09000"}
	ColorRed       = lipgloss.AdaptiveColor{Light: "#cc0000", Dark: "#ff5555"}
	ColorCyan      = lipgloss.AdaptiveColor{Light: "#0077aa", Dark: "#88d8ff"}
	ColorHighlight = lipgloss.AdaptiveColor{Light: "#dde1ff", Dark: "#1e2040"}
	ColorBg        = lipgloss.AdaptiveColor{Light: "#ffffff", Dark: "#1a1b26"}

	StyleHeader = lipgloss.NewStyle().
			Background(lipgloss.AdaptiveColor{Light: "#3b4abf", Dark: "#1a1b2e"}).
			Foreground(lipgloss.Color("#ffffff")).
			Bold(true).
			Padding(0, 1)

	StyleSubtitle = lipgloss.NewStyle().
			Background(lipgloss.AdaptiveColor{Light: "#4a5ac8", Dark: "#252640"}).
			Foreground(lipgloss.AdaptiveColor{Light: "#ccccff", Dark: "#9999cc"}).
			Padding(0, 1)

	StyleDim    = lipgloss.NewStyle().Foreground(ColorMuted)
	StyleCyan   = lipgloss.NewStyle().Foreground(ColorCyan).Bold(true)
	StyleRed    = lipgloss.NewStyle().Foreground(ColorRed).Bold(true)
	StyleBold   = lipgloss.NewStyle().Bold(true)
	StyleSelect = lipgloss.NewStyle().Background(ColorHighlight)

	StyleDialog = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(ColorPrimary).
			Padding(1, 2)

	StyleConfirm = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(ColorWarning).
			Padding(1, 2)

	StyleMenu = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(ColorPrimary).
			Padding(0, 1)

	StyleMenuSelected = lipgloss.NewStyle().
				Background(ColorHighlight).
				Padding(0, 1)

	StyleMenuNormal = lipgloss.NewStyle().
			Padding(0, 1)

	StyleFindHighlight = lipgloss.NewStyle().
				Background(lipgloss.Color("3")).
				Foreground(lipgloss.Color("0")).
				Bold(true)

	StylePane = lipgloss.NewStyle().
			BorderRight(true).
			BorderStyle(lipgloss.NormalBorder()).
			BorderForeground(ColorMuted)

	StyleFooter = lipgloss.NewStyle().
			Foreground(ColorMuted).
			Padding(0, 1)
)
