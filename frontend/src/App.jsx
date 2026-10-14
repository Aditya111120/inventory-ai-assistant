import {
  Component,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useState,
  useId,
} from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  MessageSquarePlus,
  Moon,
  Send,
  Sun,
  Database,
  PackageSearch,
  MoreHorizontal,
  LogOut,
  UserRound,
  CircleHelp,
  BookOpen,
  X,
  Search,
  MapPin,
  Tag,
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

const LEADERSHIP_FAQS = [
  {
    title: "Inventory",
    questions: [
      "Show inventory by category.",
      "Which products are slow-moving?",
      "Which categories have the highest inventory value?",
    ],
  },
  {
    title: "Sales",
    questions: [
      "Show sales revenue trends by month.",
      "Compare sales by category for last month.",
      "Which brands have the highest sales revenue?",
    ],
  },
  {
    title: "Stockout risk",
    questions: [
      "Which products have high or medium stockout risk?",
      "Show stockout risk by store.",
      "Which products have coverage below supplier lead time?",
    ],
  },
  {
    title: "What-if analysis",
    questions: [
      "What if demand increases by 10% for a product?",
      "What if supplier lead time increases by 5 days?",
      "What if sales increase by 10% for a selected brand?",
    ],
  },
];

function ProfileMenu({ user, logoutLoading, logoutError, onLogout }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);
  const triggerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsideClick = (event) => {
      if (!menuRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="profile-control" ref={menuRef}>
      <button
        ref={triggerRef}
        type="button"
        className="header-icon-button"
        aria-label="Open profile"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="profile-menu"
        onClick={() => setOpen((value) => !value)}
      >
        <UserRound size={18} aria-hidden="true" />
      </button>
      {open && (
        <section className="profile-menu" id="profile-menu" role="dialog" aria-label="Profile">
          <div className="profile-menu-heading">
            <strong>{user.display_name || "Account"}</strong>
            <button type="button" className="icon-button-small" aria-label="Close profile" onClick={() => setOpen(false)}>
              <X size={15} />
            </button>
          </div>
          <div className="profile-detail"><span>Account</span><strong>{user.account_id || user.username || "Not provided"}</strong></div>
          <div className="profile-detail"><span>Role</span><strong>{roleLabel(user.role || user.persona)}</strong></div>
          {user.full_access === true && (
            <div className="profile-detail"><span>Access</span><strong>Full Authorized Access</strong></div>
          )}
          {logoutError && <div className="logout-error" role="alert">{logoutError}</div>}
          <button type="button" className="profile-logout" onClick={onLogout} disabled={logoutLoading}>
            <LogOut size={15} />
            {logoutLoading ? "Logging out…" : "Log out"}
          </button>
        </section>
      )}
    </div>
  );
}

function HelpPanel({ view, onClose, onSelectQuestion, onAskQuestion }) {
  if (!view) return null;
  return (
    <section className="help-panel" aria-labelledby="help-panel-title">
      <div className="help-panel-header">
        <div>
          <h2 id="help-panel-title">{view === "faqs" ? "Frequently asked questions" : "How to ask"}</h2>
        </div>
        <button type="button" className="icon-button-small" onClick={onClose} aria-label="Close help panel">
          <X size={17} />
        </button>
      </div>
      {view === "faqs" ? (
        <div className="faq-groups">
          {LEADERSHIP_FAQS.map((group) => (
            <section className="faq-group" key={group.title} aria-labelledby={`faq-${group.title.replaceAll(" ", "-")}`}>
              <h3 id={`faq-${group.title.replaceAll(" ", "-")}`}>{group.title}</h3>
              <div className="faq-questions">
                {group.questions.map((item) => (
                  <div className="faq-question" key={item}>
                    <button type="button" className="faq-question-text" onClick={() => onSelectQuestion(item)}>
                      {item}
                    </button>
                    <button type="button" className="faq-send" onClick={() => onAskQuestion(item)} aria-label={`Ask: ${item}`}>
                      <Send size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="ask-guide">
          <p>Ask for one measurable result, and include the metric, entity, location, or period when you know it.</p>
          <div className="ask-example unclear">
            <strong>Unclear</strong>
            <span>“How are we doing?”</span>
          </div>
          <div className="ask-example clearer">
            <strong>More specific</strong>
            <span>“Compare sales revenue by category for September 2026.”</span>
          </div>
          <div className="ask-example unclear">
            <strong>Unclear</strong>
            <span>“Show me the risks.”</span>
          </div>
          <div className="ask-example clearer">
            <strong>More specific</strong>
            <span>“Which products have high or medium stockout risk in Mumbai?”</span>
          </div>
          <p className="help-note">If a store-level question needs a location, the assistant can ask you to choose an authorized city and store. You can then use contextual follow-ups such as “What about another brand?” or “Now show last month.”</p>
        </div>
      )}
    </section>
  );
}

async function getExploreData(path, signal) {
  const response = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    signal,
  });
  if (!response.ok) {
    throw new Error(response.status === 401
      ? "Your session has expired. Please sign in again."
      : "Explore Data could not be loaded. Please try again.");
  }
  return response.json();
}

function exploreQuestion(selection) {
  if (selection.kind === "product") return `Show inventory for ${selection.name}.`;
  if (selection.kind === "brand") return `Show inventory for ${selection.name}.`;
  if (selection.kind === "category") return `Show inventory for ${selection.name} category.`;
  if (selection.kind === "subcategory") return `Show inventory for ${selection.name} subcategory.`;
  if (selection.kind === "city") return `Show inventory in ${selection.name}.`;
  if (selection.kind === "region") return `Show inventory in ${selection.name} region.`;
  if (selection.kind === "store") return `Show inventory at ${selection.name}.`;
  if (selection.kind === "sales-period") return `Show sales from ${selection.start} to ${selection.end}.`;
  if (selection.kind === "inventory-date") return `Show inventory as of ${selection.date}.`;
  return `Show inventory snapshot for ${selection.date}.`;
}

function ExploreDataView({ onClose, onAskAbout }) {
  const [section, setSection] = useState("products");
  const [catalogFilters, setCatalogFilters] = useState({ brands: [], categories: [], subcategories: [] });
  const [locations, setLocations] = useState([]);
  const [availability, setAvailability] = useState(null);
  const [selected, setSelected] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [initialError, setInitialError] = useState("");
  const [search, setSearch] = useState("");
  const [brand, setBrand] = useState("");
  const [category, setCategory] = useState("");
  const [subcategory, setSubcategory] = useState("");
  const [page, setPage] = useState(1);
  const [products, setProducts] = useState({ items: [], total: 0, pages: 0, page: 1, page_size: 25 });
  const [productsLoading, setProductsLoading] = useState(true);
  const [productsError, setProductsError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getExploreData("/explore/filters", controller.signal),
      getExploreData("/explore/locations", controller.signal),
      getExploreData("/explore/availability", controller.signal),
    ])
      .then(([availableFilters, availableLocations, dataAvailability]) => {
        setCatalogFilters(availableFilters);
        setLocations(availableLocations.items || []);
        setAvailability(dataAvailability);
      })
      .catch((error) => {
        if (error.name !== "AbortError") setInitialError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setInitialLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ page: String(page), page_size: "25" });
    if (search.trim()) params.set("search", search.trim());
    if (brand) params.set("brand", brand);
    if (category) params.set("category", category);
    if (subcategory) params.set("subcategory", subcategory);
    getExploreData(`/explore/products?${params}`, controller.signal)
      .then((result) => {
        setProductsError("");
        setProducts(result);
      })
      .catch((error) => {
        if (error.name !== "AbortError") setProductsError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setProductsLoading(false);
      });
    return () => controller.abort();
  }, [search, brand, category, subcategory, page]);

  const cities = [...new Set(locations.map((item) => item.city).filter(Boolean))].sort();
  const regions = [...new Set(locations.map((item) => item.region).filter(Boolean))].sort();
  const sections = [
    ["products", "Products"],
    ["brands", "Brands & Categories"],
    ["locations", "Locations"],
    ["availability", "Data Availability"],
  ];

  return (
    <section className="explore-data" aria-labelledby="explore-data-title">
      <div className="explore-header">
        <div>
          <div className="welcome-label">Browse authorized catalog and data coverage</div>
          <h2 id="explore-data-title">Explore Data</h2>
        </div>
        <button type="button" className="icon-button-small" onClick={onClose} aria-label="Close Explore Data">
          <X size={17} />
        </button>
      </div>
      <div className="explore-tabs" role="tablist" aria-label="Explore data sections">
        {sections.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={section === key}
            className={section === key ? "active" : ""}
            onClick={() => setSection(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {selected && (
        <div className="explore-selection">
          <span>Selected: <strong>{selected.name || selected.label}</strong></span>
          <button type="button" onClick={() => onAskAbout(exploreQuestion(selected))}>Ask about this</button>
          <button type="button" className="explore-clear-selection" onClick={() => setSelected(null)}>Clear</button>
        </div>
      )}

      {initialError && <p className="explore-error" role="alert">{initialError}</p>}
      {section === "products" && (
        <div className="explore-section" role="tabpanel">
          <div className="explore-product-filters">
            <label className="explore-search">
              <Search size={15} aria-hidden="true" />
              <input aria-label="Search products" value={search} onChange={(event) => { setProductsLoading(true); setProductsError(""); setSearch(event.target.value); setPage(1); }} placeholder="Search SKU, product or brand" />
            </label>
            <select aria-label="Filter by brand" value={brand} onChange={(event) => { setProductsLoading(true); setProductsError(""); setBrand(event.target.value); setPage(1); }}>
              <option value="">All brands</option>
              {catalogFilters.brands.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <select aria-label="Filter by category" value={category} onChange={(event) => { setProductsLoading(true); setProductsError(""); setCategory(event.target.value); setPage(1); }}>
              <option value="">All categories</option>
              {catalogFilters.categories.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <select aria-label="Filter by subcategory" value={subcategory} onChange={(event) => { setProductsLoading(true); setProductsError(""); setSubcategory(event.target.value); setPage(1); }}>
              <option value="">All subcategories</option>
              {catalogFilters.subcategories.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </div>
          {productsError && <p className="explore-error" role="alert">{productsError}</p>}
          <div className="explore-table-wrap">
            <table className="explore-table">
              <thead><tr><th>SKU</th><th>Product</th><th>Brand</th><th>Category</th><th>Subcategory</th><th>Status</th></tr></thead>
              <tbody>
                {products.items.map((item) => (
                  <tr key={item.product_id}>
                    <td>{item.sku || "—"}</td>
                    <td><button type="button" className="explore-entity-link" onClick={() => setSelected({ kind: "product", name: item.product_name })}>{item.product_name || "Unnamed product"}</button></td>
                    <td>{item.brand || "—"}</td>
                    <td>{item.category || "—"}</td>
                    <td>{item.subcategory || "—"}</td>
                    <td>{item.is_active ? "Active" : "Inactive"}</td>
                  </tr>
                ))}
                {!productsLoading && !products.items.length && <tr><td colSpan="6" className="explore-empty">No catalog products match these filters.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="explore-pagination">
            <span>{productsLoading ? "Loading products…" : `${products.total.toLocaleString()} catalog products`}</span>
            <div>
              <button type="button" onClick={() => { setProductsLoading(true); setProductsError(""); setPage((current) => Math.max(1, current - 1)); }} disabled={page <= 1 || productsLoading}>Previous</button>
              <span>Page {page} of {Math.max(products.pages, 1)}</span>
              <button type="button" onClick={() => { setProductsLoading(true); setProductsError(""); setPage((current) => current + 1); }} disabled={page >= products.pages || productsLoading}>Next</button>
            </div>
          </div>
        </div>
      )}
      {section === "brands" && (
        <div className="explore-section explore-entity-groups" role="tabpanel">
          {[
            ["Brands", "brand", catalogFilters.brands],
            ["Categories", "category", catalogFilters.categories],
            ["Subcategories", "subcategory", catalogFilters.subcategories],
          ].map(([title, kind, values]) => (
            <section key={title} className="explore-entity-group">
              <h3><Tag size={15} />{title}</h3>
              {values.length ? (
                <div className="explore-chips">{values.map((value) => (
                  <button key={value} type="button" className={selected?.name === value ? "selected" : ""} onClick={() => setSelected({ kind, name: value })}>{value}</button>
                ))}</div>
              ) : <p className="explore-muted">No authorized {title.toLowerCase()} are available.</p>}
            </section>
          ))}
          {initialLoading && <p className="explore-muted">Loading catalog filters…</p>}
        </div>
      )}
      {section === "locations" && (
        <div className="explore-section explore-locations" role="tabpanel">
          <section className="explore-entity-group">
            <h3><MapPin size={15} />Cities</h3>
            <div className="explore-chips">{cities.map((value) => <button key={value} type="button" onClick={() => setSelected({ kind: "city", name: value })}>{value}</button>)}</div>
          </section>
          <section className="explore-entity-group">
            <h3><MapPin size={15} />Regions</h3>
            <div className="explore-chips">{regions.map((value) => <button key={value} type="button" onClick={() => setSelected({ kind: "region", name: value })}>{value}</button>)}</div>
          </section>
          <section className="explore-entity-group">
            <h3>Authorized stores</h3>
            <div className="explore-table-wrap">
              <table className="explore-table">
                <thead><tr><th>Store</th><th>City</th><th>Region</th></tr></thead>
                <tbody>{locations.map((item) => (
                  <tr key={item.store_id}>
                    <td><button type="button" className="explore-entity-link" onClick={() => setSelected({ kind: "store", name: item.store_name })}>{item.store_name}</button></td>
                    <td>{item.city || "—"}</td><td>{item.region || "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {!locations.length && !initialLoading && <p className="explore-muted">No authorized locations are available.</p>}
            {initialLoading && <p className="explore-muted">Loading authorized locations…</p>}
          </section>
        </div>
      )}
      {section === "availability" && (
        <div className="explore-section" role="tabpanel">
          {initialLoading && <p className="explore-muted">Loading data availability…</p>}
          {availability && (
            <>
              <div className="explore-counts">
                <div><span>Catalog products</span><strong>{availability.catalog_products.toLocaleString()}</strong><small>Products in the authorized catalog</small></div>
                <div><span>Products with inventory records</span><strong>{availability.inventory_products.toLocaleString()}</strong><small>Catalog products represented in inventory</small></div>
                <div><span>Products with positive stock</span><strong>{availability.positive_stock_products.toLocaleString()}</strong><small>Inventory records with quantity above zero</small></div>
              </div>
              <div className="explore-coverage">
                {[
                  ["Inventory", "inventory", "inventory-date"],
                  ["Sales", "sales", "sales-period"],
                  ["Weekly snapshots", "snapshots", "snapshot-date"],
                ].map(([label, key, kind]) => {
                  const period = availability.coverage[key];
                  const lastDate = period?.last_date;
                  return (
                    <div key={key} className="explore-coverage-item">
                      <div><strong>{label}</strong><span>{period?.first_date && lastDate ? `${period.first_date} — ${lastDate}` : "No reporting dates available"}</span></div>
                      {lastDate && <button type="button" onClick={() => setSelected(kind === "sales-period"
                        ? { kind, name: `${label} date coverage`, start: period.first_date || lastDate, end: lastDate }
                        : { kind, name: `${label} reporting date`, date: lastDate })}>Select date coverage</button>}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
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

const CHART_COLORS = ["#7652c6", "#db8b39", "#3d9b78", "#cf5e78", "#4b83bd", "#8f6c37"];

function chartNumber(value, series) {
  if (!Number.isFinite(value)) return "—";
  if (series?.unit === "currency" && series.currency) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: series.currency,
        maximumFractionDigits: 2,
      }).format(value);
    } catch {
      return `${value.toLocaleString()} ${series.currency}`;
    }
  }
  if (series?.unit === "percent") return `${value.toLocaleString()}%`;
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

function validVisualization(spec) {
  if (!spec || !["kpi", "bar", "line", "donut", "grouped_bar", "scatter"].includes(spec.type)) return false;
  if (typeof spec.title !== "string" || typeof spec.description !== "string") return false;
  if (!Array.isArray(spec.series) || !spec.series.length || !Array.isArray(spec.data) || !spec.data.length) return false;
  if (spec.series.some((item) => !item || typeof item.key !== "string" || typeof item.label !== "string"
    || (item.unit === "currency" && typeof item.currency !== "string"))) return false;
  if (spec.type === "kpi") return spec.data.every((point) => point && typeof point.label === "string"
    && Number.isFinite(point.value) && !(point.unit === "currency" && typeof point.currency !== "string"));
  if (spec.type === "donut") {
    return spec.data.length <= 6 && spec.data.every((point) => point && typeof point.label === "string" && Number.isFinite(point.count) && point.count >= 0);
  }
  if (spec.type === "line") {
    return typeof spec.date_field === "string"
      && spec.series.length === 1
      && spec.data.every((point) => point && typeof point[spec.date_field] === "string"
        && Number.isFinite(Date.parse(point[spec.date_field]))
        && Number.isFinite(point[spec.series[0].key]));
  }
  if (spec.type === "scatter") {
    return spec.series.length >= 2
      && spec.data.every((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y));
  }
  return typeof spec.category_field === "string"
    && spec.data.every((point) => point && typeof point[spec.category_field] === "string"
      && spec.series.every((series) => Number.isFinite(point[series.key])));
}

function Visualization({ spec }) {
  if (!validVisualization(spec)) return null;
  return (
    <ChartErrorBoundary>
      <ChartGraphic spec={spec} />
    </ChartErrorBoundary>
  );
}

function ChartGraphic({ spec }) {
  const width = 760;
  const height = 300;
  const left = 170;
  const isCategoryChart = spec.type === "bar" || spec.type === "grouped_bar";
  const longestValueLabel = isCategoryChart
    ? Math.max(...spec.data.flatMap((point) => spec.series.map((series) => chartNumber(point[series.key], series).length)))
    : 0;
  const right = isCategoryChart ? Math.min(width * 0.4, Math.max(30, longestValueLabel * 6 + 16)) : 30;
  const top = 24;
  const bottom = 35;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const colors = spec.series.map((_, index) => CHART_COLORS[index % CHART_COLORS.length]);
  const legend = (
    <div className="visualization-legend">
      {spec.series.map((series, index) => (
        <span key={series.key}>
          <i style={{ "--series-color": colors[index] }} />
          {series.label}
        </span>
      ))}
    </div>
  );

  let graphic = null;
  if (spec.type === "kpi") {
    graphic = (
      <div className="visualization-kpis">
        {spec.data.map((point) => (
          <div className="visualization-kpi" key={point.label}>
            <span>{point.label}</span>
            <strong>{chartNumber(point.value, point)}</strong>
          </div>
        ))}
      </div>
    );
  } else if (spec.type === "donut") {
    const total = spec.data.reduce((sum, point) => sum + point.count, 0);
    if (total <= 0) return null;
    const circumference = 2 * Math.PI * 68;
    graphic = (
      <div className="visualization-donut-layout">
        <svg className="visualization-svg donut-svg" viewBox="0 0 180 180" role="img" aria-label={spec.title}>
          <g transform="rotate(-90 90 90)">
            {spec.data.map((point, index) => {
              const offset = spec.data
                .slice(0, index)
                .reduce((sum, item) => sum + circumference * item.count / total, 0);
              const length = circumference * point.count / total;
              return (
                <circle
                  key={point.label}
                  cx="90"
                  cy="90"
                  r="68"
                  fill="none"
                  stroke={CHART_COLORS[index % CHART_COLORS.length]}
                  strokeWidth="28"
                  strokeDasharray={`${length} ${circumference - length}`}
                  strokeDashoffset={-offset}
                >
                  <title>{`${point.label}: ${chartNumber(point.count, { unit: "units" })}`}</title>
                </circle>
              );
            })}
          </g>
          <text x="90" y="86" textAnchor="middle" className="donut-total">{total.toLocaleString()}</text>
          <text x="90" y="105" textAnchor="middle" className="donut-total-label">positions</text>
        </svg>
        <div className="visualization-legend donut-legend">
          {spec.data.map((point, index) => (
            <span key={point.label}>
              <i style={{ "--series-color": CHART_COLORS[index % CHART_COLORS.length] }} />
              {point.label}: {chartNumber(point.count, { unit: "units" })}
            </span>
          ))}
        </div>
      </div>
    );
  } else if (spec.type === "line") {
    const points = spec.data
      .map((item) => ({ ...item, timestamp: Date.parse(item[spec.date_field]) }))
      .sort((a, b) => a.timestamp - b.timestamp);
    if (points.length < 2) return null;
    const values = points.map((point) => point[spec.series[0].key]);
    const minValue = Math.min(0, ...values);
    const maxValue = Math.max(1, ...values);
    const x = (index) => left + index * plotWidth / (points.length - 1);
    const y = (value) => top + (maxValue - value) / (maxValue - minValue || 1) * plotHeight;
    graphic = (
      <>
        <svg className="visualization-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
          {[0, 0.5, 1].map((fraction) => {
            const value = maxValue - fraction * (maxValue - minValue);
            const yPosition = top + fraction * plotHeight;
            return (
              <g key={fraction}>
                <line x1={left} y1={yPosition} x2={width - right} y2={yPosition} className="chart-gridline" />
                <text x={left - 10} y={yPosition + 4} textAnchor="end" className="chart-axis-label">
                  {chartNumber(value, spec.series[0])}
                </text>
              </g>
            );
          })}
          <polyline
            fill="none"
            stroke={colors[0]}
            strokeWidth="3"
            points={points.map((point, index) => `${x(index)},${y(point[spec.series[0].key])}`).join(" ")}
          />
          {points.map((point, index) => (
            <g key={point[spec.date_field]}>
              <circle cx={x(index)} cy={y(point[spec.series[0].key])} r="5" fill={colors[0]}>
                <title>{`${point[spec.date_field]}: ${chartNumber(point[spec.series[0].key], spec.series[0])}`}</title>
              </circle>
              <text x={x(index)} y={height - 10} textAnchor="middle" className="chart-axis-label">{point[spec.date_field]}</text>
            </g>
          ))}
        </svg>
        {legend}
      </>
    );
  } else if (spec.type === "scatter") {
    const xValues = spec.data.map((point) => point.x);
    const yValues = spec.data.map((point) => point.y);
    const xMin = Math.min(0, ...xValues);
    const xMax = Math.max(1, ...xValues);
    const yMin = Math.min(0, ...yValues);
    const yMax = Math.max(1, ...yValues);
    graphic = (
      <svg className="visualization-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
        <line x1={left} y1={top + plotHeight} x2={width - right} y2={top + plotHeight} className="chart-axis" />
        <line x1={left} y1={top} x2={left} y2={top + plotHeight} className="chart-axis" />
        {spec.data.map((point, index) => (
          <circle
            key={`${point.label}-${index}`}
            cx={left + (point.x - xMin) / (xMax - xMin || 1) * plotWidth}
            cy={top + plotHeight - (point.y - yMin) / (yMax - yMin || 1) * plotHeight}
            r="5"
            fill={colors[0]}
            opacity=".78"
          >
            <title>{`${point.label}: ${spec.series[0].label} ${chartNumber(point.x, spec.series[0])}, ${spec.series[1].label} ${chartNumber(point.y, spec.series[1])}`}</title>
          </circle>
        ))}
        <text x={left + plotWidth / 2} y={height - 6} textAnchor="middle" className="chart-axis-label">{spec.series[0].label}</text>
        <text x="16" y={top + plotHeight / 2} transform={`rotate(-90 16 ${top + plotHeight / 2})`} textAnchor="middle" className="chart-axis-label">{spec.series[1].label}</text>
      </svg>
    );
  } else {
    const values = spec.data.flatMap((point) => spec.series.map((series) => point[series.key]));
    const minValue = Math.min(0, ...values);
    const maxValue = Math.max(1, ...values);
    const scale = (value) => (value - minValue) / (maxValue - minValue || 1) * plotWidth;
    const baseline = left + scale(0);
    const groupHeight = Math.min(34, plotHeight / spec.data.length);
    const barHeight = Math.min(13, (groupHeight - 3) / spec.series.length);
    graphic = (
      <>
        <svg className="visualization-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
          <line x1={baseline} y1={top} x2={baseline} y2={top + plotHeight} className="chart-axis" />
          {spec.data.map((point, rowIndex) => {
            const yStart = top + rowIndex * plotHeight / spec.data.length;
            const fullLabel = point[spec.category_field];
            return (
              <g key={`${fullLabel}-${rowIndex}`}>
                <text x={left - 10} y={yStart + groupHeight / 2 + 4} textAnchor="end" className="chart-axis-label">
                  <title>{fullLabel}</title>
                  {fullLabel.length > 24 ? `${fullLabel.slice(0, 22)}…` : fullLabel}
                </text>
                {spec.series.map((series, seriesIndex) => {
                  const value = point[series.key];
                  const valueX = left + scale(value);
                  const x = Math.min(baseline, valueX);
                  const barWidth = Math.abs(valueX - baseline);
                  const y = yStart + (groupHeight - spec.series.length * barHeight) / 2 + seriesIndex * barHeight;
                  return (
                    <g key={series.key}>
                      <rect x={x} y={y} width={Math.max(barWidth, 1)} height={Math.max(barHeight - 2, 4)} fill={colors[seriesIndex]} rx="3">
                        <title>{`${fullLabel} — ${series.label}: ${chartNumber(value, series)}`}</title>
                      </rect>
                      <text x={valueX + (value >= 0 ? 5 : -5)} y={y + barHeight - 3} textAnchor={value >= 0 ? "start" : "end"} className="chart-value-label">
                        {chartNumber(value, series)}
                      </text>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
        {legend}
      </>
    );
  }

  return (
    <section className="visualization-card" aria-label="Analytical visualization">
      <div className="visualization-heading">
        <h3>{spec.title}</h3>
        {spec.description && <p>{spec.description}</p>}
      </div>
      {graphic}
      {spec.limitation && <p className="visualization-limitation">{spec.limitation}</p>}
      {spec.reporting_period && <p className="visualization-period">Reporting period: {spec.reporting_period}</p>}
    </section>
  );
}

class ChartErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <p className="visualization-fallback" role="status">Visualization unavailable; the analytical answer is still available.</p>;
    }
    return this.props.children;
  }
}

function AssistantMessage({ payload, onFollowup }) {
  const [supportingDataOpen, setSupportingDataOpen] = useState(false);
  const [supportingVisualizationOpen, setSupportingVisualizationOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const evidenceId = useId();
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
  const visualizationSpec = payload.visualization || response.visualization;
  const hasVisualization = validVisualization(visualizationSpec);
  const hasSupportingData = rows.length > 0 && !summaryOnly;

  return (
    <div className="message-row assistant-row">
      <div className="avatar assistant-avatar">
        <Bot size={17} />
      </div>

      <div className="assistant-content">
        <div className="answer-text">
          {payload.narrative || "The request completed successfully."}
        </div>

        <div className="response-evidence-row">
          <div className={`basis-pill ${simulated ? "simulated" : "actual"}`}>
            {simulated ? "Simulated" : "Actual"}
          </div>
          {hasVisualization && (
            <button
              type="button"
              className="supporting-data-button"
              aria-label="Supporting Visualization"
              aria-expanded={supportingVisualizationOpen}
              aria-controls={`${evidenceId}-visualization`}
              onClick={() => setSupportingVisualizationOpen((value) => !value)}
            >
              <PackageSearch size={14} aria-hidden="true" />
              Supporting Visualization
              {supportingVisualizationOpen
                ? <ChevronDown size={14} aria-hidden="true" />
                : <ChevronRight size={14} aria-hidden="true" />}
            </button>
          )}
          {hasSupportingData && (
            <button
              type="button"
              className="supporting-data-button"
              aria-label="Supporting Data"
              aria-expanded={supportingDataOpen}
              aria-controls={`${evidenceId}-data`}
              onClick={() => setSupportingDataOpen((value) => !value)}
            >
              <Database size={14} aria-hidden="true" />
              Supporting Data
              {supportingDataOpen
                ? <ChevronDown size={14} aria-hidden="true" />
                : <ChevronRight size={14} aria-hidden="true" />}
            </button>
          )}
        </div>

        <div id={`${evidenceId}-visualization`} hidden={!supportingVisualizationOpen}>
          {supportingVisualizationOpen && <Visualization spec={visualizationSpec} />}
        </div>

        {presentation.shared_snapshot_date && (
          <div className="snapshot-label">Stock recorded on {presentation.shared_snapshot_date}</div>
        )}

        <div id={`${evidenceId}-data`} hidden={!supportingDataOpen}>
          {supportingDataOpen && (
            <div className="result-section">
              <div className="section-label">Supporting Data</div>
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
        </div>

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
  const [helpView, setHelpView] = useState(null);
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
  const questionInputRef = useRef(null);
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
    setHelpView(null);
  }

  function selectFAQQuestion(example) {
    setQuestion(example);
    setHelpView(null);
    window.requestAnimationFrame(() => questionInputRef.current?.focus());
  }

  function selectExploreQuestion(example) {
    setQuestion(example);
    setHelpView(null);
    window.requestAnimationFrame(() => questionInputRef.current?.focus());
  }

  function askFAQQuestion(example) {
    setHelpView(null);
    window.requestAnimationFrame(() => sendQuestion(example));
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

        <div className="sidebar-help-nav" aria-label="Help">
          <button
            type="button"
            className={`sidebar-help-button ${helpView === "explore-data" ? "active" : ""}`}
            aria-pressed={helpView === "explore-data"}
            onClick={() => setHelpView((current) => current === "explore-data" ? null : "explore-data")}
          >
            <Database size={16} />
            Explore Data
          </button>
          <button
            type="button"
            className={`sidebar-help-button ${helpView === "faqs" ? "active" : ""}`}
            aria-pressed={helpView === "faqs"}
            onClick={() => setHelpView((current) => current === "faqs" ? null : "faqs")}
          >
            <CircleHelp size={16} />
            FAQs
          </button>
          <button
            type="button"
            className={`sidebar-help-button ${helpView === "how-to-ask" ? "active" : ""}`}
            aria-pressed={helpView === "how-to-ask"}
            onClick={() => setHelpView((current) => current === "how-to-ask" ? null : "how-to-ask")}
          >
            <BookOpen size={16} />
            How to Ask
          </button>
        </div>

      </aside>

      <main className="main">

        <header className="header">
          <div>
            <h1>Inventory AI Assistant</h1>
            <p>Inventory, Sales &amp; Operational Intelligence</p>
          </div>

          <div className="header-actions">
            <button
              type="button"
              className="header-icon-button"
              onClick={() => setTheme((current) => current === "light" ? "dark" : "light")}
              aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
              aria-pressed={theme === "dark"}
              title={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
            >
              {theme === "light" ? <Moon size={18} aria-hidden="true" /> : <Sun size={18} aria-hidden="true" />}
            </button>
            <ProfileMenu user={user} logoutLoading={logoutLoading} logoutError={logoutError} onLogout={handleLogout} />
          </div>
        </header>

        <div className="accent-line" />

        <HelpPanel
          view={helpView === "faqs" || helpView === "how-to-ask" ? helpView : null}
          onClose={() => setHelpView(null)}
          onSelectQuestion={selectFAQQuestion}
          onAskQuestion={askFAQQuestion}
        />
        {helpView === "explore-data" && (
          <ExploreDataView onClose={() => setHelpView(null)} onAskAbout={selectExploreQuestion} />
        )}

        {!hasConversation && !helpView && (
          <section className="landing">

            <div className="welcome">
              <div className="welcome-label">
                C5i Inventory Intelligence
              </div>

              <h2>
                What would you like to explore today?
              </h2>

              <p>
                Ask questions about inventory, sales, product performance, and operational risks. Get clear, data-backed answers to support better decisions.
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

        {hasConversation && !helpView && (
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

        {!helpView && <div className="composer-area">

          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              sendQuestion(question);
            }}
          >
            <input
              ref={questionInputRef}
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
        }

        <footer>
          C5i · Human · AI · Impact
        </footer>
      </main>
    </div>
  );
}

export default App;
