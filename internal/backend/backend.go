package backend

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
	"os/exec"
)

var (
	homeDir     string
	projectsDir string
	namesDir    string
	trashDir    string
)

func init() {
	homeDir, _ = os.UserHomeDir()
	projectsDir = filepath.Join(homeDir, ".claude", "projects")
	namesDir = filepath.Join(homeDir, ".claude", "session-names")
	trashDir = filepath.Join(homeDir, ".claude", "trash")
	os.MkdirAll(namesDir, 0755)
	os.MkdirAll(trashDir, 0755)
}

// Session represents a Claude Code session.
type Session struct {
	SID       string
	Name      string
	FirstMsg  string
	Age       string
	Msgs      int
	ProjectID string
	SortTime  string
}

func (s Session) ProjectPath() string { return PidToPath(s.ProjectID) }
func (s Session) DisplayLabel() string {
	if s.Name != "" {
		return s.Name
	}
	if s.FirstMsg != "" {
		return s.FirstMsg
	}
	if len(s.SID) > 24 {
		return s.SID[:24]
	}
	return s.SID
}

// TrashEntry represents a trashed session.
type TrashEntry struct {
	SID       string
	Name      string
	ProjectID string
	When      string
}

func (e TrashEntry) ProjectPath() string { return PidToPath(e.ProjectID) }

// PidToPath converts a project ID (encoded path) back to a human-readable path.
func PidToPath(pid string) string {
	if pid == "" {
		return ""
	}
	p := pid
	if strings.HasPrefix(p, "-") {
		p = "/" + p[1:]
	}
	p = strings.ReplaceAll(p, "-", "/")
	if homeDir != "" && strings.HasPrefix(p, homeDir) {
		p = "~" + p[len(homeDir):]
	}
	return p
}

// computeAge calculates human-readable age from a timestamp string.
func computeAge(ts string) string {
	if ts == "" {
		return "?"
	}
	var t time.Time
	if n, err := strconv.ParseFloat(ts, 64); err == nil {
		if n > 1e10 {
			t = time.Unix(int64(n)/1000, 0)
		} else {
			t = time.Unix(int64(n), 0)
		}
	} else {
		var err2 error
		t, err2 = time.Parse(time.RFC3339Nano, ts)
		if err2 != nil {
			t, err2 = time.Parse(time.RFC3339, ts)
			if err2 != nil {
				return "?"
			}
		}
	}
	diff := time.Since(t)
	if diff < 0 {
		diff = 0
	}
	if diff >= 24*time.Hour {
		return fmt.Sprintf("%dd", int(diff.Hours()/24))
	}
	if diff >= time.Hour {
		return fmt.Sprintf("%dh", int(diff.Hours()))
	}
	return fmt.Sprintf("%dm", int(diff.Minutes()))
}

type jsonlLine struct {
	Type      string          `json:"type"`
	Timestamp json.RawMessage `json:"timestamp"`
	Message   struct {
		Content json.RawMessage `json:"content"`
	} `json:"message"`
}

type contentBlock struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

func extractContent(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	var blocks []contentBlock
	if err := json.Unmarshal(raw, &blocks); err == nil {
		for _, b := range blocks {
			if b.Type == "text" && b.Text != "" {
				return b.Text
			}
		}
	}
	return ""
}

// parseJSONL parses a .jsonl file and returns (sortTime, msgCount, firstUserMsg).
func parseJSONL(path string) (string, int, string) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, ""
	}
	defer f.Close()

	var sortTime string
	var msgs int
	var firstMsg string

	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var rec jsonlLine
		if err := json.Unmarshal(line, &rec); err != nil {
			continue
		}
		if rec.Type == "user" || rec.Type == "assistant" {
			msgs++
		}
		if len(rec.Timestamp) > 0 {
			var tsStr string
			if err := json.Unmarshal(rec.Timestamp, &tsStr); err == nil {
				sortTime = tsStr
			} else {
				var tsNum float64
				if err2 := json.Unmarshal(rec.Timestamp, &tsNum); err2 == nil {
					sortTime = strconv.FormatFloat(tsNum, 'f', 0, 64)
				}
			}
		}
		if firstMsg == "" && rec.Type == "user" {
			content := extractContent(rec.Message.Content)
			content = strings.ReplaceAll(content, "\n", " ")
			content = strings.TrimSpace(content)
			if len(content) > 60 {
				content = content[:60]
			}
			firstMsg = content
		}
	}
	return sortTime, msgs, firstMsg
}

// GetNames loads the name map for a project.
func GetNames(pid string) map[string]string {
	path := filepath.Join(namesDir, pid+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]string{}
	}
	var m map[string]string
	if err := json.Unmarshal(data, &m); err != nil {
		return map[string]string{}
	}
	return m
}

// SetName sets a custom name for a session (atomic write).
func SetName(pid, sid, name string) error {
	os.MkdirAll(namesDir, 0755)
	path := filepath.Join(namesDir, pid+".json")
	m := GetNames(pid)
	m[sid] = name
	return writeJSON(path, m)
}

// RmName removes the custom name for a session.
func RmName(pid, sid string) error {
	path := filepath.Join(namesDir, pid+".json")
	m := GetNames(pid)
	delete(m, sid)
	return writeJSON(path, m)
}

// writeJSON atomically writes a JSON file.
func writeJSON(path string, v interface{}) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// ProjectForSession finds the project ID for a session by globbing.
func ProjectForSession(sid string) string {
	pattern := filepath.Join(projectsDir, "*", sid+".jsonl")
	matches, err := filepath.Glob(pattern)
	if err != nil || len(matches) == 0 {
		return ""
	}
	return filepath.Base(filepath.Dir(matches[0]))
}

// ListSessions loads and returns all sessions.
func ListSessions(cwd string, allProjects bool) []Session {
	trashed := map[string]bool{}
	trashFiles, _ := filepath.Glob(filepath.Join(trashDir, "*.jsonl"))
	for _, tf := range trashFiles {
		sid := strings.TrimSuffix(filepath.Base(tf), ".jsonl")
		trashed[sid] = true
	}

	projectDirs, _ := filepath.Glob(filepath.Join(projectsDir, "*"))

	var pidFilter string
	if !allProjects && cwd != "" {
		pidFilter = strings.ReplaceAll(cwd, "/", "-")
	}

	var sessions []Session
	for _, pdir := range projectDirs {
		info, err := os.Stat(pdir)
		if err != nil || !info.IsDir() {
			continue
		}
		pid := filepath.Base(pdir)
		if pidFilter != "" && pid != pidFilter {
			continue
		}

		names := GetNames(pid)
		jsonlFiles, _ := filepath.Glob(filepath.Join(pdir, "*.jsonl"))
		for _, jf := range jsonlFiles {
			sid := strings.TrimSuffix(filepath.Base(jf), ".jsonl")
			if trashed[sid] {
				continue
			}
			sortTime, msgs, firstMsg := parseJSONL(jf)
			if msgs == 0 {
				continue
			}
			sessions = append(sessions, Session{
				SID:       sid,
				Name:      names[sid],
				FirstMsg:  firstMsg,
				Age:       computeAge(sortTime),
				Msgs:      msgs,
				ProjectID: pid,
				SortTime:  sortTime,
			})
		}
	}

	sort.Slice(sessions, func(i, j int) bool {
		return sessions[i].SortTime > sessions[j].SortTime
	})
	return sessions
}

// ListTrash returns all trashed sessions.
func ListTrash() []TrashEntry {
	files, _ := filepath.Glob(filepath.Join(trashDir, "*.jsonl"))

	type metaFile struct {
		ProjectID string `json:"project_id"`
		Name      string `json:"name"`
		TrashedAt int64  `json:"trashed_at"`
	}

	type entryWithTime struct {
		e         TrashEntry
		trashedAt int64
	}

	var entries []entryWithTime

	for _, f := range files {
		sid := strings.TrimSuffix(filepath.Base(f), ".jsonl")
		var meta metaFile
		metaPath := filepath.Join(trashDir, sid+".meta")
		if data, err := os.ReadFile(metaPath); err == nil {
			json.Unmarshal(data, &meta)
		}
		if meta.TrashedAt == 0 {
			if info, err := os.Stat(f); err == nil {
				meta.TrashedAt = info.ModTime().Unix()
			}
		}
		when := computeAge(strconv.FormatInt(meta.TrashedAt, 10))
		entries = append(entries, entryWithTime{
			e: TrashEntry{
				SID:       sid,
				Name:      meta.Name,
				ProjectID: meta.ProjectID,
				When:      when + " ago",
			},
			trashedAt: meta.TrashedAt,
		})
	}

	sort.Slice(entries, func(i, j int) bool {
		return entries[i].trashedAt > entries[j].trashedAt
	})

	result := make([]TrashEntry, len(entries))
	for i, e := range entries {
		result[i] = e.e
	}
	return result
}

// PreviewSession generates a markdown preview of a session.
func PreviewSession(sid string) string {
	var path string
	pattern1 := filepath.Join(projectsDir, "*", sid+".jsonl")
	if m, _ := filepath.Glob(pattern1); len(m) > 0 {
		path = m[0]
	} else {
		tp := filepath.Join(trashDir, sid+".jsonl")
		if _, err := os.Stat(tp); err == nil {
			path = tp
		}
	}
	if path == "" {
		return "Session not found."
	}

	pid := ProjectForSession(sid)
	name := ""
	if pid != "" {
		names := GetNames(pid)
		name = names[sid]
	}
	projectPath := PidToPath(pid)

	var sb strings.Builder
	if name != "" {
		fmt.Fprintf(&sb, "## %s\n`%s`\n\n", name, projectPath)
	} else {
		fmt.Fprintf(&sb, "## `%s`\n\n", projectPath)
	}

	f, err := os.Open(path)
	if err != nil {
		return sb.String()
	}
	defer f.Close()

	const msgCap = 3000
	const turnCap = 30
	turns := 0
	capped := false

	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	for scanner.Scan() {
		if turns >= turnCap {
			capped = true
			break
		}
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var rec jsonlLine
		if err := json.Unmarshal(line, &rec); err != nil {
			continue
		}
		if rec.Type != "user" && rec.Type != "assistant" {
			continue
		}
		content := extractContent(rec.Message.Content)
		if content == "" {
			continue
		}
		if len(content) > msgCap {
			content = content[:msgCap]
		}
		sb.WriteString("---\n\n")
		if rec.Type == "user" {
			sb.WriteString("**You**\n\n")
			for _, line := range strings.Split(content, "\n") {
				fmt.Fprintf(&sb, "> %s\n", line)
			}
		} else {
			sb.WriteString("**Claude**\n\n")
			sb.WriteString(content)
		}
		sb.WriteString("\n\n")
		turns++
	}

	if capped {
		sb.WriteString("*Preview capped at 30 messages.*\n")
	}
	return sb.String()
}

// SearchSessions searches session content using ripgrep.
func SearchSessions(query, cwd string, allProjects, useRegex bool, caseMode string) []Session {
	rgPath, err := exec.LookPath("rg")
	if err != nil {
		return nil
	}

	var searchPath string
	if !allProjects && cwd != "" {
		pid := strings.ReplaceAll(cwd, "/", "-")
		searchPath = filepath.Join(projectsDir, pid)
	} else {
		searchPath = projectsDir
	}

	if _, err := os.Stat(searchPath); err != nil {
		return nil
	}

	args := []string{"--files-with-matches"}
	switch caseMode {
	case "ignore":
		args = append(args, "--ignore-case")
	case "match":
		args = append(args, "--case-sensitive")
	default: // "smart"
		args = append(args, "--smart-case")
	}
	if !useRegex {
		args = append(args, "--fixed-strings")
	}
	args = append(args, query, searchPath)

	out, err := exec.Command(rgPath, args...).Output()
	if err != nil {
		return nil
	}

	trashed := map[string]bool{}
	trashFiles, _ := filepath.Glob(filepath.Join(trashDir, "*.jsonl"))
	for _, tf := range trashFiles {
		sid := strings.TrimSuffix(filepath.Base(tf), ".jsonl")
		trashed[sid] = true
	}

	var sessions []Session
	seen := map[string]bool{}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if !strings.HasSuffix(line, ".jsonl") {
			continue
		}
		sid := strings.TrimSuffix(filepath.Base(line), ".jsonl")
		if trashed[sid] || seen[sid] {
			continue
		}
		seen[sid] = true
		pid := filepath.Base(filepath.Dir(line))
		names := GetNames(pid)
		sortTime, msgs, firstMsg := parseJSONL(line)
		if msgs == 0 {
			continue
		}
		sessions = append(sessions, Session{
			SID:       sid,
			Name:      names[sid],
			FirstMsg:  firstMsg,
			Age:       computeAge(sortTime),
			Msgs:      msgs,
			ProjectID: pid,
			SortTime:  sortTime,
		})
	}

	sort.Slice(sessions, func(i, j int) bool {
		return sessions[i].SortTime > sessions[j].SortTime
	})
	return sessions
}

// TrashSession moves a session to the trash.
func TrashSession(sid string) error {
	pattern := filepath.Join(projectsDir, "*", sid+".jsonl")
	matches, err := filepath.Glob(pattern)
	if err != nil || len(matches) == 0 {
		return fmt.Errorf("session %s not found", sid)
	}
	src := matches[0]
	pid := filepath.Base(filepath.Dir(src))

	names := GetNames(pid)
	name := names[sid]

	type metaStruct struct {
		ProjectID string `json:"project_id"`
		Name      string `json:"name"`
		TrashedAt int64  `json:"trashed_at"`
	}
	m := metaStruct{ProjectID: pid, Name: name, TrashedAt: time.Now().Unix()}
	metaData, _ := json.Marshal(m)
	metaPath := filepath.Join(trashDir, sid+".meta")
	if err := os.WriteFile(metaPath, metaData, 0644); err != nil {
		return err
	}

	dst := filepath.Join(trashDir, sid+".jsonl")
	if err := os.Rename(src, dst); err != nil {
		return err
	}

	sidecar := filepath.Join(filepath.Dir(src), sid)
	if info, err := os.Stat(sidecar); err == nil && info.IsDir() {
		os.Rename(sidecar, filepath.Join(trashDir, sid))
	}

	RmName(pid, sid)
	return nil
}

// RestoreSession restores a session from trash.
func RestoreSession(sid string) error {
	src := filepath.Join(trashDir, sid+".jsonl")
	if _, err := os.Stat(src); err != nil {
		return fmt.Errorf("trashed session %s not found", sid)
	}

	type metaStruct struct {
		ProjectID string `json:"project_id"`
		Name      string `json:"name"`
	}
	var m metaStruct
	metaPath := filepath.Join(trashDir, sid+".meta")
	if data, err := os.ReadFile(metaPath); err == nil {
		json.Unmarshal(data, &m)
	}

	if m.ProjectID == "" {
		dirs, _ := filepath.Glob(filepath.Join(projectsDir, "*"))
		if len(dirs) > 0 {
			m.ProjectID = filepath.Base(dirs[0])
		}
	}

	if m.ProjectID == "" {
		return fmt.Errorf("cannot determine project for session %s", sid)
	}

	projDir := filepath.Join(projectsDir, m.ProjectID)
	os.MkdirAll(projDir, 0755)

	dst := filepath.Join(projDir, sid+".jsonl")
	if err := os.Rename(src, dst); err != nil {
		return err
	}

	sidecar := filepath.Join(trashDir, sid)
	if info, err := os.Stat(sidecar); err == nil && info.IsDir() {
		os.Rename(sidecar, filepath.Join(projDir, sid))
	}

	os.Remove(metaPath)

	if m.Name != "" {
		SetName(m.ProjectID, sid, m.Name)
	}
	return nil
}

// EmptyTrash deletes all trashed sessions and returns count.
func EmptyTrash() int {
	jsonlFiles, _ := filepath.Glob(filepath.Join(trashDir, "*.jsonl"))
	count := 0
	for _, f := range jsonlFiles {
		if err := os.Remove(f); err == nil {
			count++
		}
	}
	metaFiles, _ := filepath.Glob(filepath.Join(trashDir, "*.meta"))
	for _, f := range metaFiles {
		os.Remove(f)
	}
	return count
}

// DeleteFromTrash permanently deletes a single session from the trash.
func DeleteFromTrash(sid string) error {
	os.Remove(filepath.Join(trashDir, sid+".jsonl"))
	os.Remove(filepath.Join(trashDir, sid+".meta"))
	sidecar := filepath.Join(trashDir, sid)
	if info, err := os.Stat(sidecar); err == nil && info.IsDir() {
		os.RemoveAll(sidecar)
	}
	return nil
}
