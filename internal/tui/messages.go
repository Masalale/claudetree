package tui

import "github.com/Masalale/claudetree/internal/backend"

// switchScreenMsg navigates to a different screen.
type switchScreenMsg struct {
	screen       ScreenKind
	cwd          string
	allProjects  bool
	session      *backend.Session // for preview screen
	initialQuery string           // for preview from search
}

// openDialogMsg requests a dialog overlay.
type openDialogMsg struct {
	kind          dialogKind
	prompt        string
	initialValue  string
	pendingAction string // what to do when dialog confirms
	pendingData   string // extra data (e.g. sid)
}

// previewDebounceMsg is sent after debounce delay to trigger preview load.
type previewDebounceMsg struct {
	token int
	sid   string
}

// previewLoadedMsg carries the glamour-rendered preview.
type previewLoadedMsg struct {
	content string
	token   int
}

// searchResultsMsg carries search results.
type searchResultsMsg struct {
	results []backend.Session
}

// reloadSessionsMsg requests a session list reload.
type reloadSessionsMsg struct{}

// reloadTrashMsg requests trash list reload.
type reloadTrashMsg struct{}

// ctxMenuShowMsg requests the context menu to be shown.
type ctxMenuShowMsg struct {
	options []MenuOption
	x, y   int
}

// appQuitMsg signals app exit with action.
type appQuitMsg struct {
	action string // "resume", "new", ""
	sid    string
}

// notifyMsg shows a transient notification.
type notifyMsg struct {
	text string
}
