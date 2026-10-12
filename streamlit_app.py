from __future__ import annotations

import os
import uuid
from pathlib import Path

import pandas as pd
import requests
import streamlit as st


# ============================================================
# CONFIGURATION
# ============================================================

APP_TITLE = "Inventory AI Assistant"

API_URL = os.getenv(
    "INVENTORY_API_URL",
    "http://127.0.0.1:8000",
)

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

LIGHT_LOGO = ASSETS_DIR / "c5i_logo_light.png"
DARK_LOGO = ASSETS_DIR / "c5i_logo_dark.png"


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# SESSION STATE
# ============================================================

if "session_id" not in st.session_state:
    st.session_state.session_id = (
        f"ui-{uuid.uuid4().hex[:12]}"
    )

if "messages" not in st.session_state:
    st.session_state.messages = []

if "theme" not in st.session_state:
    st.session_state.theme = "Light"


# ============================================================
# THEME
# ============================================================

THEMES = {
    "Light": {
        "bg": "#FFFFFF",
        "sidebar": "#FAF9FD",
        "surface": "#F8F6FB",
        "surface_alt": "#F1EAFA",
        "border": "#DDD7E7",
        "text": "#17131F",
        "muted": "#6F6878",
        "purple": "#6500D8",
        "purple_soft": "#F0E6FF",
        "input": "#FFFFFF",
        "hover": "#F7F0FF",
        "success_bg": "#E9F7EF",
        "success_text": "#146C43",
        "sim_bg": "#FFF1DB",
        "sim_text": "#955A00",
    },
    "Dark": {
        "bg": "#0F0D12",
        "sidebar": "#141118",
        "surface": "#18151C",
        "surface_alt": "#211A29",
        "border": "#3A3342",
        "text": "#F7F3FA",
        "muted": "#AEA6B7",
        "purple": "#913CFF",
        "purple_soft": "#2A1945",
        "input": "#19161E",
        "hover": "#241A31",
        "success_bg": "#163423",
        "success_text": "#75D79B",
        "sim_bg": "#3A2812",
        "sim_text": "#FFBE63",
    },
}

C = THEMES[st.session_state.theme]


# ============================================================
# GLOBAL CSS
# ============================================================

st.markdown(
    f"""
    <style>

    /* =========================================================
       GLOBAL
       ========================================================= */

    html,
    body,
    [class*="css"] {{
        font-family:
            Inter,
            -apple-system,
            BlinkMacSystemFont,
            "Segoe UI",
            sans-serif;
    }}

    .stApp {{
        background: {C["bg"]} !important;
        color: {C["text"]} !important;
    }}

    [data-testid="stAppViewContainer"] {{
        background: {C["bg"]} !important;
    }}

    [data-testid="stMainBlockContainer"] {{
        padding-top: 0.35rem !important;
        padding-bottom: 1rem !important;
    }}

    [data-testid="stAppViewBlockContainer"] {{
        padding-top: 0 !important;
    }}

    [data-testid="stHeader"] {{
        background: transparent !important;
    }}

    /* =========================================================
       FORCE TEXT COLORS
       ========================================================= */

    h1,
    h2,
    h3,
    h4,
    h5,
    h6,
    p,
    label,
    [data-testid="stMarkdownContainer"],
    [data-testid="stMarkdownContainer"] *,
    [data-testid="stText"],
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] * {{
        color: {C["text"]} !important;
    }}

    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] * {{
        color: {C["muted"]} !important;
    }}

    /* =========================================================
       SIDEBAR
       ========================================================= */

    [data-testid="stSidebar"] {{
        background: {C["sidebar"]} !important;
        border-right: 1px solid {C["border"]} !important;
        min-width: 220px !important;
        max-width: 235px !important;
    }}

    [data-testid="stSidebar"] > div:first-child {{
        background: {C["sidebar"]} !important;
        padding-top: 0 !important;
    }}

    [data-testid="stSidebarUserContent"] {{
        padding-top: 0 !important;
        padding-left: 0.9rem !important;
        padding-right: 0.9rem !important;
        padding-bottom: 0.5rem !important;
        margin-top: 0 !important;
    }}

    /* Hide sidebar scrollbar */
    [data-testid="stSidebar"] section {{
        overflow-y: hidden !important;
        scrollbar-width: none !important;
        -ms-overflow-style: none !important;
    }}

    [data-testid="stSidebar"] section::-webkit-scrollbar {{
        display: none !important;
        width: 0 !important;
        height: 0 !important;
    }}

    /* Hide sidebar collapse control */
    [data-testid="stSidebarCollapseButton"] {{
        display: none !important;
    }}

    /* Sidebar logo */
    [data-testid="stSidebar"] img {{
        margin-top: 0 !important;
        margin-bottom: 0.25rem !important;
        display: block !important;
    }}

    [data-testid="stSidebar"] [data-testid="stImage"] {{
        margin-top: 0 !important;
        padding-top: 0 !important;
    }}

    .sidebar-section {{
        color: {C["muted"]} !important;
        font-size: 9px !important;
        font-weight: 800 !important;
        letter-spacing: 0.10em !important;
        text-transform: uppercase !important;
        margin-top: 14px !important;
        margin-bottom: 7px !important;
    }}

    .sidebar-capability {{
        color: {C["muted"]} !important;
        font-size: 11.5px !important;
        line-height: 1.35 !important;
        margin: 2px 0 !important;
    }}

    /* =========================================================
       HEADER
       ========================================================= */

    .app-title {{
        color: {C["text"]} !important;
        font-size: 28px !important;
        font-weight: 750 !important;
        line-height: 1.05 !important;
        letter-spacing: -0.03em !important;
        margin: 0 !important;
    }}

    .app-subtitle {{
        color: {C["muted"]} !important;
        font-size: 13px !important;
        margin-top: 4px !important;
    }}

    .accent-line {{
        height: 2px;
        width: 100%;
        background: {C["purple"]};
        margin-top: 9px;
        margin-bottom: 16px;
    }}

    /* =========================================================
       WELCOME CARD
       ========================================================= */

    [data-testid="stVerticalBlockBorderWrapper"] {{
        background: {C["surface"]} !important;
        border: 1px solid {C["border"]} !important;
        border-radius: 14px !important;
        padding: 0.15rem !important;
    }}

    .welcome-eyebrow {{
        color: {C["purple"]} !important;
        font-size: 10px !important;
        font-weight: 800 !important;
        letter-spacing: 0.12em !important;
        text-transform: uppercase !important;
    }}

    /* Welcome heading */
    [data-testid="stVerticalBlockBorderWrapper"] h3 {{
        color: {C["text"]} !important;
        font-size: 23px !important;
        font-weight: 700 !important;
        line-height: 1.2 !important;
        margin-top: 0.15rem !important;
        margin-bottom: 0.35rem !important;
    }}

    /* Welcome description */
    [data-testid="stVerticalBlockBorderWrapper"]
    [data-testid="stMarkdownContainer"] p {{
        color: {C["muted"]} !important;
        font-size: 13px !important;
        line-height: 1.55 !important;
    }}

    /* =========================================================
       EXAMPLE BUTTONS
       ========================================================= */

    div.stButton > button {{
        background: {C["surface"]} !important;
        color: {C["text"]} !important;
        border: 1px solid {C["border"]} !important;
        border-radius: 10px !important;
        min-height: 53px !important;
        padding: 9px 12px !important;
        font-size: 12.5px !important;
        font-weight: 550 !important;
        white-space: normal !important;
        text-align: left !important;
    }}

    div.stButton > button *,
    div.stButton > button p {{
        color: {C["text"]} !important;
    }}

    div.stButton > button:hover {{
        background: {C["hover"]} !important;
        border-color: {C["purple"]} !important;
    }}

    /* =========================================================
       CHAT
       ========================================================= */

    [data-testid="stChatMessage"] {{
        background: transparent !important;
        padding-top: 0.15rem !important;
        padding-bottom: 0.15rem !important;
    }}

    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"],
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] * {{
        color: {C["text"]} !important;
    }}

    /* =========================================================
       INPUT
       ========================================================= */

    [data-testid="stForm"] {{
        background: {C["surface"]} !important;
        border: 1px solid {C["border"]} !important;
        border-radius: 12px !important;
        padding: 5px 7px !important;
        margin-top: 6px !important;
    }}

    [data-testid="stForm"] input {{
        background: {C["input"]} !important;
        color: {C["text"]} !important;
        border: 0 !important;
        box-shadow: none !important;
        font-size: 14px !important;
    }}

    [data-testid="stForm"] input::placeholder {{
        color: {C["muted"]} !important;
        opacity: 1 !important;
    }}

    [data-testid="stForm"] button {{
        background: {C["purple"]} !important;
        color: #FFFFFF !important;
        border: 0 !important;
        border-radius: 8px !important;
        min-height: 39px !important;
        font-weight: 700 !important;
    }}

    [data-testid="stForm"] button p {{
        color: #FFFFFF !important;
    }}

    /* =========================================================
       TRUST PANEL
       ========================================================= */

    [data-testid="stExpander"] {{
        background: {C["surface"]} !important;
        border: 1px solid {C["border"]} !important;
        border-radius: 10px !important;
    }}

    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary span {{
        color: {C["text"]} !important;
    }}

    /* =========================================================
       DATAFRAME
       ========================================================= */

    [data-testid="stDataFrame"] {{
        background: {C["surface"]} !important;
        border: 1px solid {C["border"]} !important;
        border-radius: 9px !important;
        overflow: hidden !important;
    }}

    /* =========================================================
       FOOTER
       ========================================================= */

    .footer {{
        color: {C["muted"]} !important;
        text-align: center !important;
        font-size: 9px !important;
        padding-top: 10px !important;
    }}

    /* =========================================================
       REDUCE DEFAULT VERTICAL GAPS
       ========================================================= */

    div[data-testid="stVerticalBlock"] > div {{
        gap: 0.35rem;
    }}

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# HELPERS
# ============================================================

def get_logo() -> Path | None:
    logo = (
        DARK_LOGO
        if st.session_state.theme == "Dark"
        else LIGHT_LOGO
    )

    return logo if logo.exists() else None


def reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.session_id = (
        f"ui-{uuid.uuid4().hex[:12]}"
    )


@st.cache_data(ttl=10)
def check_api_health(api_url: str) -> bool:
    try:
        response = requests.get(
            f"{api_url.rstrip('/')}/health",
            timeout=2,
        )

        return response.ok

    except requests.RequestException:
        return False


def submit_question(question: str) -> None:
    question = question.strip()

    if not question:
        return

    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
        }
    )

    try:
        response = requests.post(
            f"{API_URL.rstrip('/')}/chat",
            json={
                "question": question,
                "session_id": st.session_state.session_id,
            },
            timeout=90,
        )

        response.raise_for_status()

        st.session_state.messages.append(
            {
                "role": "assistant",
                "payload": response.json(),
            }
        )

    except requests.exceptions.ConnectionError:

        st.session_state.messages.append(
            {
                "role": "assistant",
                "payload": {
                    "status": "connection_error",
                    "error": (
                        "The Inventory AI service is unavailable. "
                        "Please start the API and try again."
                    ),
                },
            }
        )

    except requests.exceptions.Timeout:

        st.session_state.messages.append(
            {
                "role": "assistant",
                "payload": {
                    "status": "timeout",
                    "error": (
                        "The request took too long to complete. "
                        "Please try again."
                    ),
                },
            }
        )

    except requests.exceptions.HTTPError as exc:

        try:
            detail = response.json()
        except Exception:
            detail = str(exc)

        st.session_state.messages.append(
            {
                "role": "assistant",
                "payload": {
                    "status": "http_error",
                    "error": f"API request failed: {detail}",
                },
            }
        )

    except Exception:

        st.session_state.messages.append(
            {
                "role": "assistant",
                "payload": {
                    "status": "error",
                    "error": (
                        "Something went wrong while "
                        "processing the request."
                    ),
                },
            }
        )


# ============================================================
# RESULT TABLE
# ============================================================

def compact_result_table(
    table: list[dict],
) -> pd.DataFrame:

    df = pd.DataFrame(table)

    if df.empty:
        return df

    column_groups = [
        [
            "product_name",
            "quantity_on_hand",
            "inventory_value",
        ],
        [
            "product_name",
            "units",
            "revenue",
        ],
        [
            "product_name",
            "days_of_inventory",
            "lead_time_days",
            "stockout_risk",
        ],
        [
            "product_name",
            "stock_status",
            "days_of_inventory",
        ],
        [
            "brand",
            "category",
            "quantity_on_hand",
        ],
    ]

    for group in column_groups:
        available = [
            column
            for column in group
            if column in df.columns
        ]

        if len(available) >= 2:
            return df[available]

    technical_columns = {
        "product_id",
        "store_id",
        "sku",
        "reorder_level",
        "max_level",
        "positions",
        "basis",
    }

    visible = [
        column
        for column in df.columns
        if column not in technical_columns
    ]

    return df[visible[:6]]


# ============================================================
# TRUST PANEL
# ============================================================

def render_trust_panel(payload: dict) -> None:

    trust = payload.get(
        "trust_panel",
        {},
    ) or {}

    with st.expander(
        "Trust Panel · How this answer was produced",
        expanded=False,
    ):

        interpretation = trust.get(
            "interpretation",
            {},
        )

        if interpretation:

            st.markdown("**Interpretation**")

            col1, col2 = st.columns(2)

            with col1:

                st.caption("Capability")

                st.write(
                    interpretation.get(
                        "capability",
                        "—",
                    )
                )

                st.caption("Metrics")

                metrics = interpretation.get(
                    "metrics",
                    [],
                )

                st.write(
                    ", ".join(metrics)
                    if metrics
                    else "—"
                )

            with col2:

                st.caption("Group by")

                st.write(
                    interpretation.get(
                        "group_by",
                        "—",
                    )
                    or "—"
                )

                st.caption("Previous result")

                st.write(
                    "Yes"
                    if interpretation.get(
                        "uses_previous_result"
                    )
                    else "No"
                )

            resolved = interpretation.get(
                "resolved_entities",
                [],
            )

            if resolved:

                st.caption(
                    "Resolved entities"
                )

                for item in resolved:
                    st.write(
                        f"• {item}"
                    )

        query = trust.get(
            "query",
            {},
        )

        if query:

            st.markdown("**Query evidence**")

            st.caption("Rows returned")

            st.write(
                query.get(
                    "row_count",
                    0,
                )
            )

            for sql in query.get(
                "sql",
                [],
            ):

                st.caption("SQL")

                st.code(
                    sql,
                    language="sql",
                )

            params = query.get(
                "params",
                [],
            )

            if params:

                st.caption("Parameters")

                for item in params:

                    st.code(
                        repr(item),
                        language="text",
                    )

            notes = query.get(
                "notes",
                [],
            )

            if notes:

                for note in notes:
                    st.caption(note)

        calculations = trust.get(
            "calculations",
            [],
        )

        if calculations:

            st.markdown("**Calculations**")

            for calc in calculations:

                st.write(
                    calc.get(
                        "metric",
                        "Metric",
                    )
                )

                if calc.get("formula"):

                    st.code(
                        calc["formula"],
                        language="text",
                    )

                values = calc.get(
                    "values",
                    {},
                )

                if values:
                    st.json(values)

                st.write(
                    f"Result: {calc.get('result')}"
                )

        rules = trust.get(
            "rules_applied",
            {},
        )

        if rules:

            st.markdown(
                "**Business rules applied**"
            )

            rule_df = pd.DataFrame(
                [
                    {
                        "Rule": key,
                        "Value": value,
                    }
                    for key, value in rules.items()
                ]
            )

            st.dataframe(
                rule_df,
                hide_index=True,
                use_container_width=True,
            )

        st.markdown("**Result basis**")

        st.write(
            trust.get(
                "basis",
                payload.get(
                    "basis",
                    "Actual",
                ),
            )
        )


# ============================================================
# ASSISTANT MESSAGE
# ============================================================

def render_assistant(payload: dict) -> None:

    status = payload.get(
        "status",
        "error",
    )

    if status != "ok":

        with st.chat_message("assistant"):

            error = payload.get(
                "error",
                "Something went wrong.",
            )

            if status == "clarification":
                st.info(error)

            elif status == "no_results":
                st.warning(error)

            elif status in {
                "connection_error",
                "timeout",
                "http_error",
                "llm_unavailable",
            }:
                st.error(error)

            else:
                st.error(error)

        return

    with st.chat_message("assistant"):

        narrative = payload.get(
            "narrative",
            "",
        )

        if narrative:
            st.markdown(
                narrative
            )
        else:
            st.write(
                "The request completed successfully."
            )

        basis = payload.get(
            "basis",
            "Actual",
        )

        if basis == "Simulated":

            st.warning(
                "Simulated scenario result"
            )

        else:

            st.success(
                "Actual business data"
            )

        table = payload.get(
            "table",
            [],
        )

        if table:

            compact_df = compact_result_table(
                table
            )

            if not compact_df.empty:

                st.markdown(
                    "#### Results"
                )

                st.dataframe(
                    compact_df,
                    hide_index=True,
                    use_container_width=True,
                )

        render_trust_panel(
            payload
        )

        if payload.get(
            "narration_fallback",
            False,
        ):

            st.caption(
                "The answer used a deterministic fallback "
                "because the generated narration could not "
                "be verified."
            )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    logo = get_logo()

    if logo:

        st.image(
            str(logo),
            width=92,
        )

    if st.button(
        "＋  New conversation",
        use_container_width=True,
    ):

        reset_conversation()
        st.rerun()

    st.markdown(
        '<div class="sidebar-section">'
        'What I can help with'
        '</div>',
        unsafe_allow_html=True,
    )

    capabilities = [
        "Inventory visibility",
        "Inventory health",
        "Stockout risk",
        "Sales trends",
        "Excess & slow-moving",
        "What-if analysis",
    ]

    for capability in capabilities:

        st.markdown(
            f'<div class="sidebar-capability">'
            f'• {capability}'
            f'</div>',
            unsafe_allow_html=True,
        )

# ============================================================
# MAIN HEADER
# ============================================================

header_left, header_right = st.columns(
    [7.5, 2],
    vertical_alignment="center",
)

with header_left:

    st.markdown(
        '<div class="app-title">'
        'Inventory AI Assistant'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="app-subtitle">'
        'Conversational analytics for retail inventory'
        '</div>',
        unsafe_allow_html=True,
    )


with header_right:

    theme = st.segmented_control(
        "Appearance",
        ["Light", "Dark"],
        default=st.session_state.theme,
        label_visibility="collapsed",
    )

    if theme != st.session_state.theme:

        st.session_state.theme = theme
        st.rerun()


st.markdown(
    '<div class="accent-line"></div>',
    unsafe_allow_html=True,
)


# ============================================================
# LANDING
# ============================================================

if not st.session_state.messages:

    with st.container(
        border=True,
    ):

        st.markdown(
            '<div class="welcome-eyebrow">'
            'C5i INVENTORY INTELLIGENCE'
            '</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            "### Ask your inventory questions in plain language."
        )

        st.write(
            "Understand inventory, sales, stockout risk, "
            "excess inventory and what-if scenarios."
        )

    st.markdown(
        "#### Try an example"
    )

    examples = [
        "Which Lenovo laptops have the lowest inventory?",
        "Which products are at risk of stockout?",
        "Which categories have the most excess inventory?",
        "What if demand increases by 20%?",
    ]

    left_col, right_col = st.columns(2)

    with left_col:

        if st.button(
            examples[0],
            use_container_width=True,
        ):
            submit_question(
                examples[0]
            )
            st.rerun()

        if st.button(
            examples[2],
            use_container_width=True,
        ):
            submit_question(
                examples[2]
            )
            st.rerun()

    with right_col:

        if st.button(
            examples[1],
            use_container_width=True,
        ):
            submit_question(
                examples[1]
            )
            st.rerun()

        if st.button(
            examples[3],
            use_container_width=True,
        ):
            submit_question(
                examples[3]
            )
            st.rerun()


# ============================================================
# CONVERSATION
# ============================================================

for message in st.session_state.messages:

    if message["role"] == "user":

        with st.chat_message("user"):

            st.write(
                message["content"]
            )

    else:

        render_assistant(
            message["payload"]
        )


# ============================================================
# INPUT
# ============================================================

st.markdown(
    "Ask about inventory, sales, stockout risk or what-if analysis."
)

with st.form(
    "chat_form",
    clear_on_submit=True,
):

    input_col, send_col = st.columns(
        [8, 1],
        vertical_alignment="center",
    )

    with input_col:

        question = st.text_input(
            "Question",
            placeholder="Type your question...",
            label_visibility="collapsed",
        )

    with send_col:

        submitted = st.form_submit_button(
            "Send",
            use_container_width=True,
        )


if submitted and question.strip():

    submit_question(
        question
    )

    st.rerun()


# ============================================================
# FOOTER
# ============================================================

st.markdown(
    '<div class="footer">'
    'C5i · Human · AI · Impact'
    '</div>',
    unsafe_allow_html=True,
)