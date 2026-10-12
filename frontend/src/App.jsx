import {
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  MessageSquarePlus,
  Moon,
  Send,
  Sun,
  Sparkles,
  Database,
  Calculator,
  ShieldCheck,
  BarChart3,
  PackageSearch,
  MoreHorizontal,
  LogOut,
} from "lucide-react";
import "./styles.css";

const API_URL =
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

const EXAMPLES_BY_PERSONA = {
  Planner: ["Which products are at risk of stockout?", "Which products have inventory coverage below supplier lead time?", "Which products need attention?"],
  "Brand Manager": ["Show my authorized brand performance", "Show my authorized brands' slow-moving products", "Compare my authorized brands' sales"],
  "Store Manager": ["Which products in my authorized region need attention?", "Which products are low on stock in my region?", "Which products are at risk of stockout in my region?"],
  Leadership: ["What are our biggest inventory risks?", "Which category has the highest inventory value?", "Which brands are driving the most inventory?"],
};

function examplesForUser(user) {
  const persona = roleLabel(user?.role || user?.persona);
  if (persona === "Brand Manager") {
    const brands = (user?.brands || []).filter(Boolean);
    if (brands.length >= 2) return [
      `How is ${brands[0]} performing?`,
      `Which ${brands[0]} products are slow-moving?`,
      `Compare ${brands[0]} and ${brands[1]} sales.`,
    ];
    if (brands.length === 1) return [
      `How is ${brands[0]} performing?`,
      `Which ${brands[0]} products are slow-moving?`,
      `Show ${brands[0]} inventory risks.`,
    ];
  }
  if (persona === "Store Manager") {
    const regions = (user?.regions || []).filter(Boolean);
    const scope = regions.length ? ` in ${regions.join(" and ")}` : " in my authorized region";
    return [
      `Show inventory by store${scope}.`,
      `Which products are below reorder level${scope}?`,
      `Show sales by store${scope}.`,
      `Which products are at risk of stockout${scope}?`,
    ];
  }
  return EXAMPLES_BY_PERSONA[persona] || EXAMPLES_BY_PERSONA.Planner;
}

function roleLabel(role) {
  return {
    STORE_MANAGER: "Store Manager",
    BRAND_MANAGER: "Brand Manager",
    PLANNER: "Planner",
    LEADERSHIP: "Leadership",
  }[role] || role || "Planner";
}

function newSessionId() {
  return `ui-${crypto.randomUUID().slice(0, 12)}`;
}

function formatRecentDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }

  if (typeof value === "number") {
    return new Intl.NumberFormat("en-IN", {
      maximumFractionDigits: 2,
    }).format(value);
  }

  return value;
}

function formatCell(value, column) {
  if (value === null || value === undefined || value === "") return "—";
  if (["revenue", "inventory_value", "cost_price", "selling_price"].includes(column)) {
    if (typeof value === "number" || !Number.isNaN(Number(value))) {
      return `INR ${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(Number(value))}`;
    }
  }
  if (column === "growth_pct" || column.endsWith("_pct")) {
    if (typeof value === "number" || !Number.isNaN(Number(value))) return `${formatNumber(Number(value))}%`;
  }
  return formatNumber(value);
}

function resultColumns(rows, intent = null) {
  if (!rows?.length) return [];

  const columns = Object.keys(rows[0]);

  // Legacy aliases remain in payloads for compatibility, but never render
  // both labels when the approved V1 field is present.
  if (columns.includes("stockout_risk_v1") && columns.includes("stockout_risk")) {
    columns.splice(columns.indexOf("stockout_risk"), 1);
  }
  if (columns.includes("inventory_status") && columns.includes("stock_status")) {
    columns.splice(columns.indexOf("stock_status"), 1);
  }

  const questionKind =
    intent?.question_kind || "lookup";

  const groupBy =
    intent?.group_by || null;

  const filters =
    intent?.filters || {};

  const hasStore = columns.includes("store_name");
  const hasProduct = columns.includes("product_name");
  if (
    hasStore &&
    (
      groupBy === "store" ||
      filters.stores?.length ||
      filters.cities?.length ||
      filters.regions?.length ||
      rows.length > 1
    )
  ) {
    const storeColumns = [
      "product_name",
      "subcategory",
      "store_name",
      "city",
      "quantity_on_hand",
      "inventory_value",
      "units",
      "revenue",
      "days_of_inventory",
      "stockout_risk",
    ].filter((column) =>
      columns.includes(column)
    );

    if (storeColumns.length >= 2) {
      return storeColumns.slice(0, 6);
    }
  }

  if (questionKind === "ranking") {
    const rankingGroups = [
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
    ];

    for (const group of rankingGroups) {
      const matching = group.filter(
        (column) =>
          columns.includes(column)
      );

      if (matching.length >= 2) {
        return matching;
      }
    }
  }

  if (
    hasProduct &&
    hasStore
  ) {
    const distribution = [
      "product_name",
      "store_name",
      "quantity_on_hand",
      "inventory_value",
    ].filter((column) =>
      columns.includes(column)
    );

    if (distribution.length >= 2) {
      return distribution.slice(0, 6);
    }
  }

  const groups = [
    ["product_name", "subcategory", "quantity_on_hand", "inventory_value"],
    ["product_name", "units", "revenue"],
    ["product_name", "days_of_inventory", "lead_time_days", "stockout_risk"],
    ["product_name", "stock_status", "days_of_inventory"],
    ["brand", "category", "quantity_on_hand"],
  ];

  for (const group of groups) {
    const matching = group.filter((x) => columns.includes(x));
    if (matching.length >= 2) return matching;
  }

  const hidden = new Set([
    "product_id",
    "store_id",
    "sku",
    "reorder_level",
    "max_level",
    "basis",
  ]);

  return columns.filter((x) => !hidden.has(x)).slice(0, 6);
}

function suggestedQuestions(payload) {
  return payload?.response?.followups || payload?.followups || [];
}

function readableColumn(column) {
  const labels = {
    product_id: "Product ID",
    sku: "SKU",
    product_name: "Product",
    subcategory: "Subcategory",
    store_id: "Store ID",
    store_name: "Store",
    city: "City",
    region: "Region",
    quantity_on_hand: "Inventory",
    inventory_value: "Inventory value",
    inventory_snapshot_date: "Snapshot date",
    units: "Units sold",
    units_sold_window: "Units sold (window)",
    avg_daily_sales: "Average daily sales",
    revenue: "Revenue",
    days_of_inventory: "Stock coverage (days)",
    actual_days_of_inventory: "Current coverage (days)",
    simulated_days_of_inventory: "Simulated coverage (days)",
    coverage_change_days: "Change (days)",
    actual_stockout_risk: "Current risk",
    simulated_stockout_risk: "Simulated risk",
    lead_time_days: "Lead time",
    stockout_risk: "Risk",
    stockout_risk_v1: "Stockout risk",
    stock_status: "Status",
    brand: "Brand",
    category: "Category",
  };

  return labels[column] || column.replaceAll("_", " ");
}

function ResultTable({ rows, intent = null, display = null }) {
  if (!rows?.length) return null;

  const rawColumns = display?.columns?.filter((column) => Object.hasOwn(rows[0], column)) || resultColumns(rows, intent);
  const columns = rawColumns.filter((column) =>
    !(column === "stockout_risk" && rawColumns.includes("stockout_risk_v1"))
    && !(column === "stock_status" && rawColumns.includes("inventory_status"))
  );

  return (
    <div className="result-table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className={`col-${column}`}>{readableColumn(column)}</th>
            ))}
          </tr>
        </thead>

        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => (
                <td key={column} className={`col-${column}`}>
                  {formatCell(row[column], column)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrustPanel({ payload }) {
  const [open, setOpen] = useState(false);
  const trust = payload?.trust_panel || {};
  const evidenceAvailable = Boolean(
    trust.interpretation && trust.query
  );

  return (
    <div className="trust-panel">
      <button
        className="trust-toggle"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="trust-toggle-left">
          <ShieldCheck size={16} />
          How this answer was produced
        </span>
        {open ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
      </button>

      {open && (
        <div className="trust-content">
          {!evidenceAvailable && (
            <div className="trust-block" role="status">
              Detailed evidence was not saved with this response.
            </div>
          )}
          <section>
            <div className="trust-heading">
              <Sparkles size={15} />
              Interpretation
            </div>

            <div className="trust-grid">
              <div>
                <span>Capability</span>
                <strong>
                  {trust.interpretation?.capability || "Not saved"}
                </strong>
              </div>

              <div>
                <span>Group by</span>
                <strong>
                  {trust.interpretation?.group_by || "Not saved"}
                </strong>
              </div>

              <div>
                <span>Metrics</span>
                <strong>
                  {trust.interpretation?.metrics?.join(", ") || "Not saved"}
                </strong>
              </div>

              <div>
                <span>Previous result</span>
                <strong>
                  {trust.interpretation?.uses_previous_result
                    ? "Yes"
                    : "No"}
                </strong>
              </div>
            </div>

            {!!trust.interpretation?.resolved_entities?.length && (
              <div className="trust-block">
                <span>Resolved entities</span>
                {trust.interpretation.resolved_entities.map(
                  (item, index) => (
                    <div key={index} className="trust-line">
                      {item}
                    </div>
                  )
                )}
              </div>
            )}
          </section>

          <section>
            <div className="trust-heading">
              <Database size={15} />
              Query evidence
            </div>

            <div className="trust-meta">
              <span>Matching result rows</span>
              <strong>
                {trust.query?.row_count ?? "Not saved"}
              </strong>
            </div>
            {trust.query?.returned_result_rows != null && (
              <div className="trust-meta">
                <span>Returned top-risk positions</span>
                <strong>{trust.query.returned_result_rows}</strong>
              </div>
            )}
            {trust.interpretation?.capability === "stockout_risk"
              && trust.query?.result_counts?.distinct_products != null && (
                <div className="trust-meta">
                  <span>Distinct products in qualifying set</span>
                  <strong>{trust.query.result_counts.distinct_products}</strong>
                </div>
              )}
            {!!trust.query?.result_counts?.product_store_positions && (
              <div className="trust-meta">
                <span>Product-store positions</span>
                <strong>{trust.query.result_counts.product_store_positions}</strong>
              </div>
            )}

            {!!trust.query?.sql?.length &&
              trust.query.sql.map((sql, index) => (
                <pre key={index} className="code-block">
                  {sql}
                </pre>
              ))}

            {!!trust.query?.params?.length && (
              <div className="trust-block">
                <span>Parameters</span>

                {trust.query.params.map((item, index) => (
                  <pre key={index} className="code-block compact">
                    {JSON.stringify(item)}
                  </pre>
                ))}
              </div>
            )}
          </section>

          {!!trust.calculations?.length && (
            <section>
              <div className="trust-heading">
                <Calculator size={15} />
                Calculations
              </div>

              {trust.calculations.map((calc, index) => (
                <div className="calc-card" key={index}>
                  <strong>{calc.metric}</strong>

                  {calc.formula && (
                    <pre className="code-block compact">
                      {calc.formula}
                    </pre>
                  )}

                  {calc.values && (
                    <pre className="code-block compact">
                      {JSON.stringify(calc.values, null, 2)}
                    </pre>
                  )}

                  <div className="calc-result">
                    Result: {formatNumber(calc.result)}
                  </div>

                  <div className="calc-basis">
                    {calc.basis || trust.basis || payload.basis}
                  </div>
                </div>
              ))}
            </section>
          )}

          {!!Object.keys(trust.rules_applied || {}).length && (
            <section>
              <div className="trust-heading">
                <BarChart3 size={15} />
                Business rules
              </div>

              <div className="rules-grid">
                {Object.entries(trust.rules_applied).map(
                  ([key, value]) => (
                    <div key={key} className="rule-item">
                      <span>{key.replaceAll("_", " ")}</span>
                      <strong>{formatNumber(value)}</strong>
                    </div>
                  )
                )}
              </div>
            </section>
          )}

          <div className="trust-footer">
            <span>Source basis</span>
            <strong>{trust.basis || payload.basis}</strong>
          </div>
        </div>
      )}
    </div>
  );
}

function AssistantMessage({ payload, onFollowup }) {
  const [expanded, setExpanded] = useState(false);
  if (!payload) return null;

  if (payload.status !== "ok") {
    return (
      <div className="message-row assistant-row">
        <div className="avatar assistant-avatar">
          <Bot size={17} />
        </div>

        <div className="error-card">
          <div>{payload.clarification_prompt || payload.error || "Something went wrong."}</div>
          {!!payload.clarification_options?.length && (
            <div className="clarification-options" role="group" aria-label="Clarification choices">
              {payload.clarification_options.map((option) => (
                <button
                  key={option.token || option.label}
                  type="button"
                  className="clarification-option"
                  onClick={() => onFollowup(option.label, option.label)}
                >
                  <span>{option.label}</span>
                  {option.description && <small>{option.description}</small>}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  const family = payload.response_type || payload.response?.response_type;
  const conversationalFamilies = new Set([
    "conversation", "capability_discovery", "terminology", "data_discovery", "catalog_discovery",
  ]);
  if (payload.response?.presentation_mode === "conversational" && conversationalFamilies.has(family)) {
    const response = payload.response || {};
    const rows = response.all_rows || payload.table || [];
    const display = { columns: response.display_columns || payload.display?.columns };
    return (
      <div className="message-row assistant-row">
        <div className="avatar assistant-avatar"><Bot size={17} /></div>
        <div className="assistant-content">
          <div className="answer-text">{payload.narrative || "I’m ready to help."}</div>
          {!!rows.length && family === "catalog_discovery" && (
            <div className="result-section">
              <div className="section-label">Catalog</div>
              <ResultTable rows={response.display_rows || rows.slice(0, 8)} intent={payload.intent} display={display} />
            </div>
          )}
          {!!payload.followups?.length && (
            <div className="suggestion-section">
              <div className="suggestion-label">Continue exploring</div>
              <div className="suggestion-list">
                {payload.followups.slice(0, 3).map((suggestion) => (
                  <button key={suggestion} type="button" className="suggestion-button" onClick={() => onFollowup(suggestion)}>
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  const simulated = payload.basis === "Simulated";
  const response = payload.response || {};
  const rows = response.all_rows || payload.table || [];
  const initialRows = response.display_rows || rows;
  const summaryOnly = response.summary_only === true || payload.display?.summary_only === true;
  const display = { columns: response.display_columns || payload.display?.columns };
  const presentation = payload.display || {};
  const supportingOnly = presentation.single_result_supporting_only === true;

  return (
    <div className="message-row assistant-row">
      <div className="avatar assistant-avatar">
        <Bot size={17} />
      </div>

      <div className="assistant-content">
        <div className="answer-text">
          {payload.narrative || "The request completed successfully."}
        </div>

        <div className={`basis-pill ${simulated ? "simulated" : "actual"}`}>
          {simulated ? "Simulated" : "Actual"}
        </div>

        {presentation.shared_snapshot_date && (
          <div className="snapshot-label">Stock recorded on {presentation.shared_snapshot_date}</div>
        )}

        {!!rows.length && !summaryOnly && (!supportingOnly || expanded) && (
          <div className="result-section">
            <div className="section-label">
              Results
            </div>

            {(() => {
              const hasMore = response.has_more === true;

              return (
                <>
                  <ResultTable rows={expanded ? rows : initialRows} intent={payload.intent} display={display} />

                  {hasMore && (
                    <button type="button" className="results-toggle" onClick={() => setExpanded((value) => !value)}>
                      {expanded
                        ? "Show less"
                        : response.available_result_count < response.total_result_count
                          ? `View ${response.available_result_count} returned rows (${response.total_result_count} matching)`
                          : `View all ${response.total_result_count || rows.length} matching results`}
                    </button>
                  )}
                </>
              );
            })()}
          </div>
        )}

        {supportingOnly && !!rows.length && (
          <button type="button" className="results-toggle" onClick={() => setExpanded((value) => !value)}>
            {expanded ? "Hide supporting record" : "Show supporting record"}
          </button>
        )}

        <TrustPanel payload={payload} />

        {suggestedQuestions(payload).length > 0 && (
          <div className="suggestion-section">
            <div className="suggestion-label">
              Continue exploring
            </div>

            <div className="suggestion-list">
              {suggestedQuestions(payload).map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  className="suggestion-button"
                  onClick={() => onFollowup(suggestion)}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function App() {
  const [theme, setTheme] = useState("light");
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [user, setUser] = useState(null);
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const accountEpochRef = useRef(0);
  const [recents, setRecents] = useState([]);
  const [activeConversation, setActiveConversation] = useState(null);
  const userId = user?.user_id;

  const loadRecents = useCallback(async () => {
    if (!userId) return;
    const requestEpoch = accountEpochRef.current;
    try {
      const response = await fetch(`${API_URL}/conversations/${userId}`, { credentials: "include" });
      if (response.ok && requestEpoch === accountEpochRef.current) {
        setRecents((await response.json()).conversations || []);
      }
    } catch { /* API availability is shown when a chat is sent. */ }
  }, [userId]);

  useEffect(() => { loadRecents(); }, [loadRecents]);

  useEffect(() => {
    fetch(`${API_URL}/auth/me`, { credentials: "include" })
      .then((response) => (response.ok ? response.json() : null))
      .then((current) => { if (current) setUser({ ...current, persona: roleLabel(current.role) }); })
      .catch(() => {});
  }, []);

  const signIn = async (event) => {
    event.preventDefault();
    setLoginError("");
    setLoginLoading(true);
    try {
      const response = await fetch(`${API_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ username: loginUsername, password: loginPassword }),
      });
      if (!response.ok) throw new Error("Invalid username or password.");
      const current = await response.json();
      accountEpochRef.current += 1;
      setUser({ ...current, persona: roleLabel(current.role) });
      setMessages([]);
      setRecents([]);
      setActiveConversation(null);
      setSessionId(newSessionId());
      setLogoutError("");
      setLoginPassword("");
    } catch (error) {
      setLoginError(error.message || "Unable to sign in.");
    } finally {
      setLoginLoading(false);
    }
  };

  const clearAccountState = useCallback(() => {
    accountEpochRef.current += 1;
    setMessages([]);
    setQuestion("");
    setRecents([]);
    setActiveConversation(null);
    setSessionId(newSessionId());
    setLoading(false);
    setLogoutError("");
    setUser(null);
    setLoginUsername("");
    setLoginPassword("");
  }, []);

  const handleLogout = useCallback(async () => {
    if (logoutLoading) return;
    setLogoutError("");
    setLogoutLoading(true);
    try {
      const response = await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) {
        throw new Error(response.status >= 500
          ? "The service could not revoke your session. Please try again."
          : "Logout was not completed. Please try again.");
      }
      clearAccountState();
    } catch (error) {
      setLogoutError(error.message || "Logout was not completed. Please try again.");
    } finally {
      setLogoutLoading(false);
    }
  }, [clearAccountState, logoutLoading]);

  const hasConversation = messages.length > 0;

  const themeClass = useMemo(
    () => `app ${theme}`,
    [theme]
  );

  const sendQuestion = useCallback(async (text, visibleText = text) => {
    const cleaned = text.trim();

    if (!cleaned || loading) return;

    setQuestion("");

    const displayText = String(visibleText || text).trim() || cleaned;
    const requestEpoch = accountEpochRef.current;

    setMessages((current) => [
      ...current,
      {
        role: "user",
        content: displayText,
      },
    ]);

    setLoading(true);

    try {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 30000);
      let response;
      try {
        response = await fetch(
          `${API_URL}/chat`,
          {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          signal: controller.signal,
          credentials: "include",
          body: JSON.stringify({
            question: cleaned,
            session_id: sessionId,
            conversation_id: sessionId,
          }),
          }
        );
      } finally {
        window.clearTimeout(timeout);
      }

      if (response.status === 401) {
        setUser(null);
        throw new Error("Your session has expired. Please sign in again.");
      }
      if (!response.ok) {
        let detail = "";
        try {
          const errorPayload = await response.json();
          detail = errorPayload?.detail || errorPayload?.error || "";
        } catch { /* Keep the user-facing status message generic. */ }
        const statusMessage = response.status >= 500
          ? "The Inventory AI service could not complete that request. Please try again."
          : response.status >= 400
            ? "The request could not be processed. Please check the question and try again."
            : "The Inventory AI service returned an unexpected response.";
        const error = new Error(statusMessage);
        error.userMessage = statusMessage;
        error.serverDetail = detail;
        throw error;
      }

      const payload = await response.json();

      if (payload.session_id) {
        setSessionId(payload.session_id);
        setActiveConversation(payload.session_id);
      }
      if (requestEpoch !== accountEpochRef.current) return;
      loadRecents();

      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          payload,
        },
      ]);
    } catch (error) {
      const networkFailure = error?.name === "TypeError";
      const timedOut = error?.name === "AbortError";
      if (requestEpoch !== accountEpochRef.current) return;
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          payload: {
            status: "error",
            error: timedOut
              ? "The request timed out. Please try again."
              : networkFailure
                ? "I couldn't connect to the Inventory AI service. Please check that FastAPI is running."
                : error?.userMessage || "The Inventory AI service could not complete that request. Please try again.",
          },
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [loading, sessionId, loadRecents]);

  function resetChat() {
    setMessages([]);
    const nextConversation = newSessionId();
    setSessionId(nextConversation);
    setActiveConversation(nextConversation);
    setQuestion("");
  }

  async function openConversation(conversationId) {
    const requestEpoch = accountEpochRef.current;
    try {
      const response = await fetch(`${API_URL}/conversations/${user.user_id}/${conversationId}`, { credentials: "include" });
      if (!response.ok) return;
      const history = (await response.json()).messages || [];
      if (requestEpoch !== accountEpochRef.current) return;
      setSessionId(conversationId);
      setActiveConversation(conversationId);
      setMessages(history.map((message) => message.role === "user"
        ? { role: "user", content: message.content }
        : { role: "assistant", payload: { status: message.metadata?.status || "ok", narrative: message.content, ...(message.metadata || {}) } }));
    } catch { /* Keep current conversation when history cannot be loaded. */ }
  }

  async function renameConversation(conversation) {
    const title = window.prompt("Rename conversation", conversation.title || "");
    if (!title?.trim()) return;
    const response = await fetch(`${API_URL}/conversations/${user.user_id}/${conversation.conversation_id}`, {
      method: "PATCH", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim() }),
    });
    if (response.ok) loadRecents();
  }

  async function deleteConversation(conversation) {
    if (!window.confirm(`Delete “${conversation.title || "this conversation"}”?`)) return;
    const response = await fetch(`${API_URL}/conversations/${user.user_id}/${conversation.conversation_id}`, { method: "DELETE", credentials: "include" });
    if (response.ok) {
      setRecents((items) => items.filter((item) => item.conversation_id !== conversation.conversation_id));
      if (activeConversation === conversation.conversation_id) resetChat();
    }
  }

  if (!user) {
    return (
      <div className={`app ${theme} login-screen`}>
        <form className="login-card" onSubmit={signIn}>
          <img className="sidebar-logo" src={theme === "dark" ? "/c5i_logo_dark.png" : "/c5i_logo_light.png"} alt="C5i" />
          <h1>Sign in to Inventory AI</h1>
          <label>Username<input value={loginUsername} onChange={(event) => setLoginUsername(event.target.value)} autoComplete="username" /></label>
          <label>Password<input type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} autoComplete="current-password" /></label>
          {loginError && <div className="login-error" role="alert">{loginError}</div>}
          <button type="submit" disabled={loginLoading}>{loginLoading ? "Signing in…" : "Sign in"}</button>
        </form>
      </div>
    );
  }

  return (
    <div className={themeClass}>
      <aside className="sidebar">

        <div className="sidebar-logo-wrap">
          <img
            className="sidebar-logo"
            src={
              theme === "dark"
                ? "/c5i_logo_dark.png"
                : "/c5i_logo_light.png"
            }
            alt="C5i"
          />
        </div>

        <div className="persona-control sidebar-profile" aria-label="Account details">
          <span className="account-display-name">{user.display_name}</span>
          <small>Account: {user.account_id || user.username}</small>
          <small>Role: {roleLabel(user.role || user.persona)}</small>
          {user.role === "STORE_MANAGER" && <small>Region: {(user.regions || []).join(", ") || "Not assigned"}</small>}
          {user.role === "BRAND_MANAGER" && <small>Brands: {(user.brands || []).join(", ") || "Not assigned"}</small>}
          {(user.role === "PLANNER" || user.role === "LEADERSHIP") && <small>Full access</small>}
        </div>

        <button
          className="new-chat-button"
          onClick={resetChat}
        >
          <MessageSquarePlus size={17} />
          New chat
        </button>

        {recents.length > 0 && <>
          <div className="sidebar-section-title">Recents</div>
          <div className="recent-list">
            {recents.map((conversation) => (
              <div className={`recent-item ${activeConversation === conversation.conversation_id ? "active" : ""}`} key={conversation.conversation_id}>
                <button className="recent-button" onClick={() => openConversation(conversation.conversation_id)} title={conversation.first_question || conversation.title}>
                  <span className="recent-title">{conversation.title || "Untitled conversation"}</span>
                  <small className="recent-updated">{formatRecentDate(conversation.updated_at)}</small>
                </button>
                <details className="recent-menu">
                  <summary aria-label="Conversation actions"><MoreHorizontal size={15} /></summary>
                  <div className="recent-menu-popover">
                    <button type="button" onClick={() => renameConversation(conversation)}>Rename</button>
                    <button type="button" onClick={() => deleteConversation(conversation)}>Delete</button>
                  </div>
                </details>
              </div>
            ))}
          </div>
        </>}

        <div className="sidebar-section-title">
          What I can help with
        </div>

        <div className="capability-list">
          <div>Inventory visibility</div>
          <div>Inventory health</div>
          <div>Stockout risk</div>
          <div>Sales trends</div>
          <div>Excess & slow-moving</div>
          <div>What-if analysis</div>
        </div>

        <div className="sidebar-bottom">
          <div className="sidebar-status">
            <span className="status-dot" />
            Inventory data connected
          </div>
          {logoutError && <div className="logout-error" role="alert">{logoutError}</div>}
          <button className="logout-button" type="button" onClick={handleLogout} disabled={logoutLoading}>
            <LogOut size={15} />
            {logoutLoading ? "Logging out…" : "Log out"}
          </button>
        </div>
      </aside>

      <main className="main">

        <header className="header">
          <div>
            <h1>Inventory AI Leadership Assistant</h1>
            <p>Inventory, Sales &amp; Operational Intelligence</p>
          </div>

          <div className="theme-control">
            <button
              className={theme === "light" ? "active" : ""}
              onClick={() => setTheme("light")}
              aria-label="Light theme"
            >
              <Sun size={15} />
              Light
            </button>

            <button
              className={theme === "dark" ? "active" : ""}
              onClick={() => setTheme("dark")}
              aria-label="Dark theme"
            >
              <Moon size={15} />
              Dark
            </button>
          </div>
        </header>

        <div className="accent-line" />

        {!hasConversation && (
          <section className="landing">

            <div className="welcome">
              <div className="welcome-label">
                C5i Inventory Intelligence
              </div>

              <h2>
                Inventory Intelligence for Better Decisions
              </h2>

              <p>
                Explore inventory performance, sales trends, stock availability, and operational risks through data-backed insights.
              </p>
            </div>

            <div className="examples-heading">
              Try an example
            </div>

            <div className="example-grid">
              {examplesForUser(user).map((example) => (
                <button
                  key={example}
                  className="example-card"
                  onClick={() => sendQuestion(example)}
                >
                  <PackageSearch size={16} />
                  <span>{example}</span>
                </button>
              ))}
            </div>
          </section>
        )}

        {hasConversation && (
          <section className="conversation">
            {messages.map((message, index) => {
              if (message.role === "user") {
                return (
                  <div
                    className="message-row user-row"
                    key={index}
                  >
                    <div className="avatar user-avatar">
                      <span>U</span>
                    </div>

                    <div className="user-message">
                      {message.content}
                    </div>
                  </div>
                );
              }

              return (
                <AssistantMessage
                  key={index}
                  payload={message.payload}
                  onFollowup={sendQuestion}
                />
              );
            })}

            {loading && (
              <div className="message-row assistant-row">
                <div className="avatar assistant-avatar">
                  <Bot size={17} />
                </div>

                <div className="typing">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
          </section>
        )}

        <div className="composer-area">

          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              sendQuestion(question);
            }}
          >
            <input
              type="text"
              value={question}
              onChange={(event) =>
                setQuestion(event.target.value)
              }
              placeholder="Ask about inventory, sales, stockout risk or what-if analysis..."
              disabled={loading}
            />

            <button
              type="submit"
              disabled={!question.trim() || loading}
              aria-label="Send question"
            >
              <Send size={17} />
            </button>
          </form>

          <div className="composer-note">
            Analysis and decision support only · No inventory changes are made
          </div>
        </div>

        <footer>
          C5i · Human · AI · Impact
        </footer>
      </main>
    </div>
  );
}

export default App;
