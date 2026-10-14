from pathlib import Path


APP = Path(__file__).parents[1] / "frontend" / "src" / "App.jsx"
STYLES = Path(__file__).parents[1] / "frontend" / "src" / "styles.css"


def test_sidebar_logout_uses_server_session_and_clears_account_state():
    source = APP.read_text(encoding="utf-8")
    assert '`${API_URL}/auth/logout`' in source
    assert 'method: "POST"' in source
    assert 'credentials: "include"' in source
    assert "setMessages([])" in source
    assert "setRecents([])" in source
    assert "setActiveConversation(null)" in source
    assert "setUser(null)" in source
    assert "<LogOut" in source
    assert 'className="profile-logout"' in source
    assert "Log out" in source
    styles = STYLES.read_text(encoding="utf-8")
    assert ".recent-list" in styles and "overflow-y: auto" in styles


def test_welcome_and_recent_history_are_readable():
    source = APP.read_text(encoding="utf-8")
    assert "Inventory AI Assistant" in source
    assert "What would you like to explore today?" in source
    assert "Ask questions about inventory, sales, product performance, and operational risks. Get clear, data-backed answers to support better decisions." in source
    assert "Inventory, Sales &amp; Operational Intelligence" in source
    assert "conversation.first_question || conversation.title" in source
    assert "formatRecentDate(conversation.updated_at)" in source
    assert "activeConversation === conversation.conversation_id" in source
    assert "onFollowup(option.label, option.label)" in source


def test_account_card_renders_role_scoped_metadata():
    source = APP.read_text(encoding="utf-8")
    assert "{user.account_id || user.username || " in source
    assert "roleLabel(user.role || user.persona)" in source
    assert "{user.full_access === true && (" in source
    assert "Full Authorized Access" in source
