from claudetree.backend import Session
from claudetree.presentation import (
    CommandSpec,
    filter_commands,
    session_row_text,
    status_strip_text,
)


def test_session_row_text_includes_summary_metadata():
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


def test_status_strip_text_summarizes_command_center_state():
    text = status_strip_text(
        screen_label="browse",
        scope_label="all projects",
        sort_label="recent",
        filter_label="auth",
        count=18,
        mode_label="browse",
        command_hint="Ctrl+K palette",
    )

    assert "browse" in text.plain
    assert "all projects" in text.plain
    assert "recent" in text.plain
    assert "auth" in text.plain
    assert "18" in text.plain
    assert "Ctrl+K" in text.plain


def test_filter_commands_uses_fuzzy_matching_and_ordering():
    commands = [
        CommandSpec(key="preview", label="Open preview", keywords=("open", "resume")),
        CommandSpec(key="trash", label="Trash session", keywords=("remove", "bin")),
        CommandSpec(key="rename", label="Rename session", keywords=("label", "name")),
    ]

    filtered = filter_commands("rnm", commands)

    assert [cmd.key for cmd in filtered] == ["rename"]

    filtered = filter_commands("trsh", commands)
    assert [cmd.key for cmd in filtered] == ["trash"]
