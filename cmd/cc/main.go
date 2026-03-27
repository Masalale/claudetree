package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"syscall"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/Masalale/claudetree/internal/backend"
	"github.com/Masalale/claudetree/internal/tui"
)

func main() {
	args := os.Args[1:]

	if len(args) == 0 {
		runTUI(tui.ScreenBrowse, true, cwd())
		return
	}

	switch args[0] {
	case "rm", "delete", "trash":
		if len(args) > 1 {
			if err := backend.TrashSession(args[1]); err != nil {
				fmt.Fprintln(os.Stderr, "Error:", err)
				os.Exit(1)
			}
			fmt.Println("Trashed.")
		} else {
			runTUI(tui.ScreenTrash, true, cwd())
		}
	case "restore":
		if len(args) > 1 {
			if err := backend.RestoreSession(args[1]); err != nil {
				fmt.Fprintln(os.Stderr, "Error:", err)
				os.Exit(1)
			}
			fmt.Println("Restored.")
		} else {
			runTUI(tui.ScreenTrash, true, cwd())
		}
	case "empty":
		fmt.Print("Empty all trash? [y/N] ")
		reader := bufio.NewReader(os.Stdin)
		answer, _ := reader.ReadString('\n')
		answer = strings.TrimSpace(strings.ToLower(answer))
		if answer == "y" {
			n := backend.EmptyTrash()
			fmt.Printf("Deleted %d session(s).\n", n)
		} else {
			fmt.Println("Cancelled.")
		}
	case "help", "-h", "--help":
		printHelp()
	case "ls", "list":
		runTUI(tui.ScreenBrowse, true, cwd())
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", args[0])
		printHelp()
		os.Exit(1)
	}
}

func cwd() string {
	dir, _ := os.Getwd()
	return dir
}

func runTUI(initialScreen tui.ScreenKind, allProjects bool, cwdPath string) {
	m := tui.NewAppModel(initialScreen, allProjects, cwdPath)
	p := tea.NewProgram(m, tea.WithAltScreen(), tea.WithMouseCellMotion())
	finalModel, err := p.Run()
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error running TUI:", err)
		os.Exit(1)
	}

	app, ok := finalModel.(tui.AppModel)
	if !ok || app.Result == nil {
		return
	}

	claudeCmd := os.Getenv("CLAUDE_CMD")
	if claudeCmd == "" {
		claudeCmd = "claude"
	}
	claudePath, err := exec.LookPath(claudeCmd)
	if err != nil {
		fmt.Fprintln(os.Stderr, "claude not found in PATH")
		os.Exit(1)
	}

	switch app.Result.Action {
	case "resume":
		syscall.Exec(claudePath, []string{claudeCmd, "--resume", app.Result.SID}, os.Environ())
	case "new":
		syscall.Exec(claudePath, []string{claudeCmd}, os.Environ())
	}
}

func printHelp() {
	fmt.Println(`claudetree — Claude Code session manager

USAGE:
  cc                    Browse all sessions (TUI)
  cc rm [sid]           Trash a session (or open trash browser)
  cc restore [sid]      Restore a session (or open trash browser)
  cc empty              Empty all trash (with confirmation)
  cc help               Show this help

TUI KEYBINDINGS (Browse):
  j/k or ↑/↓           Navigate sessions
  enter                 Open session preview
  /                     Filter sessions
  ctrl+s                Cycle sort order
  ctrl+d                Trash session
  ctrl+r                Rename session
  ctrl+t                Open trash bin
  ctrl+a                Toggle project filter / directory picker
  ctrl+/                Content search
  ctrl+n                New Claude session
  q / esc               Quit

TUI KEYBINDINGS (Preview):
  enter                 Resume session
  ctrl+f                Find in preview
  n/N                   Next/previous match
  ctrl+i                Cycle case mode
  ctrl+g                Toggle regex/literal
  esc                   Back

Environment:
  CLAUDE_CMD            Override claude binary path (default: claude)
`)
}
