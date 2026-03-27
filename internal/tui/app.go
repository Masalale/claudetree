package tui

import (
	"strings"

	"github.com/Masalale/claudetree/internal/backend"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// ScreenKind identifies which screen is active.
type ScreenKind int

const (
	ScreenBrowse    ScreenKind = iota
	ScreenPreview
	ScreenDirPicker
	ScreenSearch
	ScreenTrash
)

// internal aliases for use within the tui package
const (
	screenBrowse    = ScreenBrowse
	screenPreview   = ScreenPreview
	screenDirPicker = ScreenDirPicker
	screenSearch    = ScreenSearch
	screenTrash     = ScreenTrash
)

type dialogKind int

const (
	dialogNone    dialogKind = iota
	dialogInput
	dialogConfirm
)

// AppResult holds the exit action.
type AppResult struct {
	Action string // "resume", "new", ""
	SID    string
}

// AppModel is the root Bubbletea model.
type AppModel struct {
	screen    ScreenKind
	browse    BrowseModel
	preview   PreviewModel
	dirpicker DirPickerModel
	search    SearchModel
	trash     TrashModel

	dialog     dialogKind
	inputDlg   InputDialogModel
	confirmDlg ConfirmDialogModel
	ctxMenu    ContextMenuModel

	pendingAction string
	pendingData   string

	width, height int
	Result        *AppResult
	notify        string
}

func NewAppModel(initialScreen ScreenKind, allProjects bool, cwd string) AppModel {
	m := AppModel{
		screen:    initialScreen,
		browse:    NewBrowseModel(cwd, allProjects),
		trash:     NewTrashModel(),
		search:    NewSearchModel(cwd, allProjects),
		dirpicker: NewDirPickerModel(),
	}
	return m
}

func (m AppModel) Init() tea.Cmd {
	switch m.screen {
	case screenBrowse:
		return m.browse.Init()
	case screenTrash:
		return m.trash.Init()
	}
	return nil
}

func (m AppModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	// Handle window size globally
	if ws, ok := msg.(tea.WindowSizeMsg); ok {
		m.width = ws.Width
		m.height = ws.Height
		var cmds []tea.Cmd
		var cmd tea.Cmd
		m.browse, cmd = m.browse.Update(ws)
		cmds = append(cmds, cmd)
		m.preview, cmd = m.preview.Update(ws)
		cmds = append(cmds, cmd)
		m.dirpicker, cmd = m.dirpicker.Update(ws)
		cmds = append(cmds, cmd)
		m.search, cmd = m.search.Update(ws)
		cmds = append(cmds, cmd)
		m.trash, cmd = m.trash.Update(ws)
		cmds = append(cmds, cmd)
		return m, tea.Batch(cmds...)
	}

	// Handle quit
	if qmsg, ok := msg.(appQuitMsg); ok {
		m.Result = &AppResult{Action: qmsg.action, SID: qmsg.sid}
		return m, tea.Quit
	}

	// Handle screen switch
	if smsg, ok := msg.(switchScreenMsg); ok {
		return m.handleSwitch(smsg)
	}

	// Handle dialog open
	if dmsg, ok := msg.(openDialogMsg); ok {
		return m.handleOpenDialog(dmsg)
	}

	// Handle context menu show
	if cmsg, ok := msg.(ctxMenuShowMsg); ok {
		m.ctxMenu.Show(cmsg.options, cmsg.x, cmsg.y, m.width, m.height)
		return m, nil
	}

	// Handle context menu result
	if action, ok := msg.(ctxMenuDoneMsg); ok {
		return m.handleCtxMenuAction(string(action))
	}

	// Handle notify
	if nmsg, ok := msg.(notifyMsg); ok {
		m.notify = nmsg.text
		return m, nil
	}

	// Route to context menu if visible
	if m.ctxMenu.visible {
		var cmd tea.Cmd
		m.ctxMenu, cmd = m.ctxMenu.Update(msg)
		return m, cmd
	}

	// Route to dialog if open
	if m.dialog != dialogNone {
		return m.updateDialog(msg)
	}

	// Route to current screen
	return m.updateCurrentScreen(msg)
}

func (m AppModel) handleSwitch(msg switchScreenMsg) (AppModel, tea.Cmd) {
	m.screen = msg.screen
	var cmds []tea.Cmd

	switch msg.screen {
	case screenBrowse:
		if msg.cwd != "" || msg.allProjects {
			m.browse = NewBrowseModel(msg.cwd, msg.allProjects)
		}
		cmds = append(cmds, m.browse.Init())
	case screenPreview:
		if msg.session != nil {
			m.preview = NewPreviewModel(*msg.session, msg.initialQuery)
			cmds = append(cmds, m.preview.Init())
		}
	case screenDirPicker:
		m.dirpicker = NewDirPickerModel()
		cmds = append(cmds, m.dirpicker.Init())
	case screenSearch:
		m.search = NewSearchModel(m.browse.cwd, m.browse.allProjects)
		cmds = append(cmds, m.search.Init())
	case screenTrash:
		m.trash = NewTrashModel()
		cmds = append(cmds, m.trash.Init())
	}

	// propagate window size
	if m.width > 0 {
		ws := tea.WindowSizeMsg{Width: m.width, Height: m.height}
		switch msg.screen {
		case screenBrowse:
			m.browse, _ = m.browse.Update(ws)
		case screenPreview:
			m.preview, _ = m.preview.Update(ws)
		case screenDirPicker:
			m.dirpicker, _ = m.dirpicker.Update(ws)
		case screenSearch:
			m.search, _ = m.search.Update(ws)
		case screenTrash:
			m.trash, _ = m.trash.Update(ws)
		}
	}

	return m, tea.Batch(cmds...)
}

func (m AppModel) handleOpenDialog(msg openDialogMsg) (AppModel, tea.Cmd) {
	m.pendingAction = msg.pendingAction
	m.pendingData = msg.pendingData
	switch msg.kind {
	case dialogInput:
		m.dialog = dialogInput
		m.inputDlg = NewInputDialog(msg.prompt, msg.initialValue)
		return m, textinput.Blink
	case dialogConfirm:
		m.dialog = dialogConfirm
		m.confirmDlg = NewConfirmDialog(msg.prompt)
		return m, textinput.Blink
	}
	return m, nil
}

func (m AppModel) updateDialog(msg tea.Msg) (AppModel, tea.Cmd) {
	switch m.dialog {
	case dialogInput:
		if km, ok := msg.(tea.KeyMsg); ok {
			switch km.Type {
			case tea.KeyEnter:
				value := strings.TrimSpace(m.inputDlg.input.Value())
				m.dialog = dialogNone
				return m.handleDialogResult(true, value)
			case tea.KeyEsc:
				m.dialog = dialogNone
				return m, nil
			}
		}
		var cmd tea.Cmd
		m.inputDlg, cmd = m.inputDlg.Update(msg)
		return m, cmd

	case dialogConfirm:
		if km, ok := msg.(tea.KeyMsg); ok {
			switch km.Type {
			case tea.KeyEnter:
				confirmed := m.confirmDlg.input.Value() == "y"
				m.dialog = dialogNone
				if confirmed {
					return m.handleDialogResult(true, "y")
				}
				return m, nil
			case tea.KeyEsc:
				m.dialog = dialogNone
				return m, nil
			}
		}
		var cmd tea.Cmd
		m.confirmDlg, cmd = m.confirmDlg.Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m AppModel) handleDialogResult(confirmed bool, value string) (AppModel, tea.Cmd) {
	if !confirmed {
		return m, nil
	}
	switch m.pendingAction {
	case "rename":
		// pendingData = "sid|||pid"
		parts := strings.SplitN(m.pendingData, "|||", 2)
		if len(parts) == 2 {
			sid, pid := parts[0], parts[1]
			name := value
			return m, func() tea.Msg {
				backend.SetName(pid, sid, name)
				return reloadSessionsMsg{}
			}
		}
	case "delete_forever":
		sid := m.pendingData
		return m, func() tea.Msg {
			backend.DeleteFromTrash(sid)
			return reloadTrashMsg{}
		}
	case "empty_trash":
		return m, func() tea.Msg {
			backend.EmptyTrash()
			return reloadTrashMsg{}
		}
	}
	return m, nil
}

func (m AppModel) handleCtxMenuAction(action string) (AppModel, tea.Cmd) {
	if action == "" {
		return m, nil
	}
	switch {
	case action == "new":
		return m, func() tea.Msg { return appQuitMsg{action: "new"} }
	case strings.HasPrefix(action, "resume:"):
		sid := strings.TrimPrefix(action, "resume:")
		return m, func() tea.Msg { return appQuitMsg{action: "resume", sid: sid} }
	case strings.HasPrefix(action, "rename:"):
		sid := strings.TrimPrefix(action, "rename:")
		pid := backend.ProjectForSession(sid)
		names := backend.GetNames(pid)
		currentName := names[sid]
		return m, func() tea.Msg {
			return openDialogMsg{
				kind:          dialogInput,
				prompt:        "Rename session:",
				initialValue:  currentName,
				pendingAction: "rename",
				pendingData:   sid + "|||" + pid,
			}
		}
	case strings.HasPrefix(action, "trash:"):
		sid := strings.TrimPrefix(action, "trash:")
		return m, func() tea.Msg {
			backend.TrashSession(sid)
			return reloadSessionsMsg{}
		}
	case strings.HasPrefix(action, "restore:"):
		sid := strings.TrimPrefix(action, "restore:")
		return m, func() tea.Msg {
			backend.RestoreSession(sid)
			return reloadTrashMsg{}
		}
	case strings.HasPrefix(action, "delete_forever:"):
		sid := strings.TrimPrefix(action, "delete_forever:")
		return m, func() tea.Msg {
			return openDialogMsg{
				kind:          dialogConfirm,
				prompt:        "Delete forever?",
				pendingAction: "delete_forever",
				pendingData:   sid,
			}
		}
	case action == "empty_trash":
		return m, func() tea.Msg {
			return openDialogMsg{
				kind:          dialogConfirm,
				prompt:        "Empty all trash?",
				pendingAction: "empty_trash",
			}
		}
	}
	return m, nil
}

func (m AppModel) updateCurrentScreen(msg tea.Msg) (AppModel, tea.Cmd) {
	var cmd tea.Cmd
	switch m.screen {
	case screenBrowse:
		m.browse, cmd = m.browse.Update(msg)
	case screenPreview:
		m.preview, cmd = m.preview.Update(msg)
	case screenDirPicker:
		m.dirpicker, cmd = m.dirpicker.Update(msg)
	case screenSearch:
		m.search, cmd = m.search.Update(msg)
	case screenTrash:
		m.trash, cmd = m.trash.Update(msg)
	}
	return m, cmd
}

func (m AppModel) View() string {
	var baseView string
	switch m.screen {
	case screenBrowse:
		baseView = m.browse.View()
	case screenPreview:
		baseView = m.preview.View()
	case screenDirPicker:
		baseView = m.dirpicker.View()
	case screenSearch:
		baseView = m.search.View()
	case screenTrash:
		baseView = m.trash.View()
	default:
		baseView = "Loading…"
	}

	// Overlay context menu
	if m.ctxMenu.visible {
		menuView := m.ctxMenu.View(m.width, m.height)
		baseView = overlayContextMenu(baseView, menuView, m.width)
	}

	// Overlay dialog
	switch m.dialog {
	case dialogInput:
		dlgView := m.inputDlg.View(m.width, m.height)
		baseView = overlayOnBase(baseView, dlgView)
	case dialogConfirm:
		dlgView := m.confirmDlg.View(m.width, m.height)
		baseView = overlayOnBase(baseView, dlgView)
	}

	// Notification
	if m.notify != "" {
		notifyBar := lipgloss.NewStyle().
			Background(lipgloss.Color("#333300")).
			Foreground(lipgloss.Color("#ffff00")).
			Width(m.width).Render("  " + m.notify)
		lines := strings.Split(baseView, "\n")
		if len(lines) > 0 {
			lines[len(lines)-1] = notifyBar
			baseView = strings.Join(lines, "\n")
		}
	}

	return baseView
}

// overlayOnBase overlays dialog content centered on base.
func overlayOnBase(base, overlay string) string {
	if overlay == "" {
		return base
	}
	baseLines := strings.Split(base, "\n")
	overlayLines := strings.Split(overlay, "\n")

	result := make([]string, len(baseLines))
	copy(result, baseLines)

	for i, ol := range overlayLines {
		if i >= len(result) {
			break
		}
		if strings.TrimSpace(ol) == "" {
			continue
		}
		result[i] = ol
	}
	return strings.Join(result, "\n")
}
