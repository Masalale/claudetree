# Claudetree Command Center Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Transform Claudetree from a good session browser into a more powerful command center with clearer status, denser session rows, and a fuzzy command palette for fast navigation.

**Architecture:** Keep the existing session management flows intact, but add a small pure presentation layer for row/status formatting and a modal command palette for high-value actions. The main browse/search/trash screens should share the same ergonomic patterns so the app feels coherent instead of mode-heavy.

**Tech Stack:** Python 3.11, Textual, Rich, pytest.

---

### Task 1: Add pure presentation helpers for row/status rendering

**Objective:** Create a small testable module that formats session rows and top-level status text without touching the UI framework.

**Files:**
- Create: `src/claudetree/presentation.py`
- Create: `tests/test_presentation.py`

**Step 1: Write failing test**

```python
from claudetree.backend import Session
from claudetree.presentation import session_row_text, status_line


def test_session_row_text_includes_name_messages_and_project():
    session = Session(
        sid="sid-123",
        name="Refactor auth",
        first_msg="please clean up the login flow",
        age="4m",
        msgs=12,
        project_id="-home-ngash-app",
        sort_time="1",
    )

    text = session_row_text(session, show_project=True)

    assert "Refactor auth" in text.plain
    assert "12 msgs" in text.plain
    assert "~/app" in text.plain
    assert "please clean up the login flow" in text.plain


def test_status_line_summarizes_scope_sort_and_counts():
    text = status_line(scope_label="all projects", sort_label="↓ Recent", count=18, filter_text="auth")

    assert "all projects" in text.plain
    assert "↓ Recent" in text.plain
    assert "18" in text.plain
    assert "auth" in text.plain
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_presentation.py -v`
Expected: FAIL — missing module/function

**Step 3: Write minimal implementation**

```python
from rich.text import Text

# implement session_row_text and status_line
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_presentation.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/claudetree/presentation.py tests/test_presentation.py
git commit -m "feat: add presentation helpers for claudetree UI"
```

### Task 2: Add a fuzzy command palette modal

**Objective:** Let users trigger common actions with Ctrl+K instead of memorizing every binding.

**Files:**
- Modify: `src/claudetree/app.py`
- Potentially modify: `src/claudetree/presentation.py`

**Step 1: Add a small command definition structure and palette screen**

Create a modal screen that accepts a list of commands and returns the selected action key.

**Step 2: Wire Ctrl+K on browse/search/trash/preview screens**

Open the palette with screen-specific actions, then dispatch to the existing handlers.

**Step 3: Verify manually**

Run: `python -m claudetree` and press Ctrl+K from each screen.
Expected: relevant actions appear and the selected one runs.

**Step 4: Commit**

```bash
git add src/claudetree/app.py
git commit -m "feat: add command palette to claudetree"
```

### Task 3: Make session rows denser and more informative

**Objective:** Show message counts and a stronger secondary line so the browser feels like a command center.

**Files:**
- Modify: `src/claudetree/app.py`
- Modify: `src/claudetree/presentation.py`

**Step 1: Update row rendering to include summary metadata**

Add message counts and a first-message snippet beneath or beside the title.

**Step 2: Make the browse/search lists display the richer rows consistently**

Use the same renderer in browse, search, and trash screens where appropriate.

**Step 3: Verify manually**

Run the app, browse a few sessions, and confirm the list is more scannable without opening preview each time.

**Step 4: Commit**

```bash
git add src/claudetree/app.py src/claudetree/presentation.py
git commit -m "feat: enrich claudetree session rows"
```

### Task 4: Add a compact status strip for scope and mode clarity

**Objective:** Make scope, sort, filter, and search mode impossible to misunderstand.

**Files:**
- Modify: `src/claudetree/app.py`
- Modify: `src/claudetree/presentation.py`

**Step 1: Add status text generation helpers**

Create helpers for browse/search/trash status text.

**Step 2: Render the status strip in each screen**

Update it whenever filters, sort, scope, or search mode change.

**Step 3: Verify manually**

Check that the top-level text updates as you move between all-projects/current-project, sort modes, and search mode toggles.

**Step 4: Commit**

```bash
git add src/claudetree/app.py src/claudetree/presentation.py
git commit -m "feat: add status strip to claudetree"
```

### Task 5: Final verification

**Objective:** Make sure the new UI layer is stable and doesn’t break the current session flows.

**Files:**
- All touched files

**Step 1: Run the tests**

Run: `pytest tests/ -q`
Expected: PASS

**Step 2: Launch the app and manually inspect the main flows**

Check browse, preview, search, trash, rename, restore, and resume.

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: overhaul claudetree into a command center"
```
