import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";
import { after, before, test } from "node:test";
import { chromium } from "playwright";

const frontendDirectory = resolve(import.meta.dirname, "..");
const baseUrl = "http://127.0.0.1:5176";
let server;
let browser;

before(async () => {
  server = spawn(
    process.execPath,
    [resolve(frontendDirectory, "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5176", "--strictPort"],
    { cwd: frontendDirectory, stdio: "ignore" },
  );
  const startup = new Promise((resolveStartup, rejectStartup) => {
    const timeout = setTimeout(() => rejectStartup(new Error("Vite did not start")), 15000);
    const poll = async () => {
      try {
        const response = await fetch(baseUrl);
        if (response.ok) {
          clearTimeout(timeout);
          resolveStartup();
          return;
        }
      } catch {
        // Wait until Vite accepts connections.
      }
      setTimeout(poll, 150);
    };
    poll();
  });
  await startup;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
  if (server && server.exitCode === null) {
    server.kill();
    await once(server, "exit");
  }
});

test("Explore Data preserves chat state and returns entity questions to chat", async () => {
  const page = await browser.newPage();
  const requestedProductSearches = [];
  const chatRequests = [];
  await page.route("**/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      user_id: 7,
      username: "account-7",
      account_id: "account-7",
      display_name: "Test User",
      role: "LEADERSHIP",
      full_access: true,
    }),
  }));
  await page.route("**/conversations/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ conversations: [], messages: [] }),
  }));
  await page.route("**/explore/filters", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ brands: ["Example Brand"], categories: ["Laptops"], subcategories: ["Ultrabooks"] }),
  }));
  await page.route("**/explore/locations", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ items: [{ store_id: 1, store_name: "Central Store", city: "Sample City", region: "North" }] }),
  }));
  await page.route("**/explore/availability", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      catalog_products: 2,
      inventory_products: 1,
      positive_stock_products: 1,
      coverage: {
        inventory: { first_date: "2026-01-01", last_date: "2026-02-01" },
        sales: { first_date: "2026-01-02", last_date: "2026-02-02" },
        snapshots: { first_date: "2026-01-03", last_date: "2026-02-03" },
      },
    }),
  }));
  await page.route("**/explore/products**", async (route) => {
    requestedProductSearches.push(new URL(route.request().url()).searchParams.get("search"));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [{
          product_id: 1,
          sku: "SKU-1",
          product_name: "Sample Laptop",
          brand: "Example Brand",
          category: "Laptops",
          subcategory: "Ultrabooks",
          is_active: true,
        }],
        total: 1,
        page: 1,
        page_size: 25,
        pages: 1,
      }),
    });
  });
  await page.route("**/chat", async (route) => {
    chatRequests.push({
      body: route.request().postDataJSON(),
      helpViewVisible: await page.locator(".help-panel, .explore-data").count() > 0,
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", narrative: "Sample answer", session_id: "test-session" }),
    });
  });

  await page.goto(baseUrl);
  await page.getByRole("button", { name: "Explore Data" }).waitFor();
  const composerInput = page.getByRole("textbox", { name: /Ask about/ });
  await composerInput.fill("unsent draft");
  await page.getByRole("button", { name: "Explore Data" }).click();
  await page.getByRole("heading", { name: "Explore Data" }).waitFor();
  assert.equal(await page.locator(".composer-area").count(), 0);
  await page.getByRole("tab", { name: "Brands & Categories" }).click();
  await page.getByRole("button", { name: "Laptops" }).click();
  await page.getByRole("button", { name: "Ask about this" }).click();
  await page.getByRole("heading", { name: "Inventory AI Assistant" }).waitFor();
  assert.equal(await composerInput.inputValue(), "Show inventory for Laptops category.");

  await page.getByRole("button", { name: "Explore Data" }).click();
  await page.getByRole("textbox", { name: "Search products" }).fill("sample");
  await page.getByRole("combobox", { name: "Filter by brand" }).selectOption("Example Brand");
  await page.getByRole("tab", { name: "Locations" }).click();
  await page.getByText("Central Store").waitFor();
  await page.getByRole("tab", { name: "Data Availability" }).click();
  await page.getByText("Products with positive stock").waitFor();
  await page.getByRole("tab", { name: "Products" }).click();
  await page.getByRole("button", { name: "FAQs" }).click();
  assert.equal(await page.locator(".composer-area").count(), 0);
  await page.getByRole("button", { name: "Close help panel" }).click();
  assert.equal(await composerInput.inputValue(), "Show inventory for Laptops category.");

  await page.getByRole("button", { name: "FAQs" }).click();
  await page.getByRole("button", { name: "Ask: Show inventory by category." }).click();
  await page.getByText("Sample answer").waitFor();
  assert.equal(chatRequests.length, 1);
  assert.equal(chatRequests[0].body.question, "Show inventory by category.");
  assert.equal(chatRequests[0].helpViewVisible, false);
  assert.ok(requestedProductSearches.includes("sample"));
  await page.close();
});

test("analytical answers keep supporting data collapsed and show only backend rows", async () => {
  const page = await browser.newPage();
  await page.route("**/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      user_id: 7,
      username: "account-7",
      account_id: "account-7",
      display_name: "Test User",
      role: "LEADERSHIP",
      full_access: true,
    }),
  }));
  await page.route("**/conversations/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ conversations: [], messages: [] }),
  }));
  await page.route("**/chat", async (route) => {
    const { question } = route.request().postDataJSON();
    const noRows = question === "Show an empty analytical result";
    const visualization = noRows
      ? { type: "pie", title: "Malformed", series: [], data: [] }
      : {
        type: "bar",
        title: "Inventory value by category",
        description: "Sorted by value, highest first.",
        category_field: "label",
        series: [{ key: "value", label: "Inventory value", unit: "currency", currency: "INR" }],
        data: [
          { label: "Laptops", value: 1200 },
          { label: "Audio", value: 450 },
        ],
        basis: "Actual",
        complete: true,
      };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        narrative: noRows ? "No matching products were found." : "Product One has the highest inventory.",
        basis: "Actual",
        response_type: "lookup",
        intent: { capability: "inventory_visibility" },
        visualization,
        trust_panel: { query: { row_count: noRows ? 0 : 3 } },
        response: {
          response_type: "lookup",
          all_rows: noRows ? [] : [
            { product_name: "Product One", quantity_on_hand: 12 },
            { product_name: "Product Two", quantity_on_hand: 8 },
          ],
          display_rows: noRows ? [] : [
            { product_name: "Product One", quantity_on_hand: 12 },
          ],
          display_columns: ["product_name", "quantity_on_hand"],
          total_result_count: noRows ? 0 : 3,
          available_result_count: noRows ? 0 : 2,
          has_more: !noRows,
          visualization,
        },
        table: [],
        display: {},
        followups: noRows ? [] : ["Show more category details"],
      }),
    });
  });

  await page.goto(baseUrl);
  const composer = page.getByRole("textbox", { name: /Ask about/ });
  await composer.fill("Show inventory");
  await page.getByRole("button", { name: "Send question" }).click();

  const answer = page.locator(".assistant-content").filter({ hasText: "Product One has the highest inventory." });
  await answer.getByText("Actual").waitFor();
  const supportingButton = answer.getByRole("button", { name: /Supporting Data/ });
  assert.equal(await supportingButton.getAttribute("aria-expanded"), "false");
  const visualizationButton = answer.getByRole("button", { name: "Supporting Visualization" });
  assert.equal(await visualizationButton.getAttribute("aria-expanded"), "false");
  assert.equal(await answer.locator(".result-table").count(), 0);
  assert.equal(await answer.getByText("How this answer was produced").count(), 0);
  assert.equal(await answer.locator(".response-evidence-row .basis-pill").count(), 1);
  assert.equal(await answer.locator(".response-evidence-row .supporting-data-button").count(), 2);
  assert.equal(await answer.getByRole("button", { name: "Supporting Data" }).getAttribute("aria-controls") !== null, true);
  assert.equal(await visualizationButton.getAttribute("aria-controls") !== null, true);
  assert.equal(await answer.locator(".visualization-card").count(), 0);
  await visualizationButton.click();
  assert.equal(await visualizationButton.getAttribute("aria-expanded"), "true");
  const chart = answer.locator(".visualization-card");
  await chart.getByRole("heading", { name: "Inventory value by category" }).waitFor();
  assert.equal(await chart.locator("svg rect").count(), 2);
  assert.equal(
    await chart.evaluate((element) => element.parentElement.previousElementSibling.classList.contains("response-evidence-row")),
    true,
  );
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  assert.equal(await page.locator(".app.dark .visualization-card").count(), 1);
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  assert.equal(await page.locator(".app.light .visualization-card").count(), 1);

  await supportingButton.click();
  assert.equal(await supportingButton.getAttribute("aria-expanded"), "true");
  assert.equal(await visualizationButton.getAttribute("aria-expanded"), "true");
  assert.equal(await answer.locator(".result-table tbody tr").count(), 1);
  assert.ok(await answer.getByText("Product One").count());
  assert.equal(await answer.getByText("Product Two").count(), 0);
  await visualizationButton.click();
  assert.equal(await visualizationButton.getAttribute("aria-expanded"), "false");
  assert.equal(await supportingButton.getAttribute("aria-expanded"), "true");
  assert.equal(await answer.locator(".visualization-card").count(), 0);
  await answer.getByRole("button", { name: /View 2 returned rows \(3 matching\)/ }).click();
  assert.equal(await answer.locator(".result-table tbody tr").count(), 2);
  assert.equal(await answer.getByText("Product Two").count(), 1);
  await supportingButton.click();
  assert.equal(await answer.locator(".result-table").count(), 0);

  await answer.getByRole("button", { name: "Show more category details" }).click();
  await page.getByText("Product One has the highest inventory.").nth(1).waitFor();

  await composer.fill("Show an empty analytical result");
  await page.getByRole("button", { name: "Send question" }).click();
  const emptyAnswer = page.locator(".assistant-content").filter({ hasText: "No matching products were found." });
  await emptyAnswer.waitFor();
  assert.equal(await emptyAnswer.getByRole("button", { name: "Supporting Data" }).count(), 0);
  assert.equal(await emptyAnswer.getByRole("button", { name: "Supporting Visualization" }).count(), 0);
  assert.equal(await emptyAnswer.locator(".result-table").count(), 0);
  assert.equal(await emptyAnswer.locator(".visualization-card").count(), 0);

  await page.close();
});

test("validated KPI, donut, line, grouped-bar and scatter metadata renders safely", async () => {
  const page = await browser.newPage();
  await page.route("**/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      user_id: 7,
      username: "account-7",
      account_id: "account-7",
      display_name: "Test User",
      role: "LEADERSHIP",
      full_access: true,
    }),
  }));
  await page.route("**/conversations/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ conversations: [], messages: [] }),
  }));
  const charts = {
    kpi: {
      type: "kpi",
      title: "Sales summary",
      description: "",
      series: [{ key: "value", label: "Value", unit: "count" }],
      data: [{ label: "Sales revenue", value: 1240, unit: "currency", currency: "INR" }],
      basis: "Actual",
    },
    donut: {
      type: "donut",
      title: "Inventory status",
      description: "",
      category_field: "label",
      series: [{ key: "count", label: "Positions", unit: "positions" }],
      data: [{ label: "Healthy", count: 3 }, { label: "Low", count: 1 }],
      basis: "Actual",
    },
    line: {
      type: "line",
      title: "Inventory snapshots",
      description: "Two snapshots only.",
      date_field: "date",
      series: [{ key: "quantity", label: "Inventory units", unit: "units" }],
      data: [{ date: "2026-08-31", quantity: 3 }, { date: "2026-09-28", quantity: 5 }],
      basis: "Actual",
    },
    grouped_bar: {
      type: "grouped_bar",
      title: "Actual vs Simulated",
      description: "",
      category_field: "label",
      series: [{ key: "actual", label: "Actual", unit: "days" }, { key: "simulated", label: "Simulated", unit: "days" }],
      data: [{ label: "Item A", actual: 12, simulated: 8 }],
      basis: "Simulated",
    },
    scatter: {
      type: "scatter",
      title: "Inventory vs value",
      description: "",
      series: [{ key: "x", label: "Inventory", unit: "units" }, { key: "y", label: "Value", unit: "currency", currency: "INR" }],
      data: [{ label: "A", x: 2, y: 20 }, { label: "B", x: 4, y: 40 }, { label: "C", x: 6, y: 60 }],
      basis: "Actual",
    },
    malformed: {
      type: "bar",
      title: "Malformed chart",
      category_field: "label",
      series: [{ key: "value", label: "Revenue", unit: "currency" }],
      data: [{ label: "Unknown currency", value: 100 }],
    },
  };
  await page.route("**/chat", async (route) => {
    const { question } = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        narrative: `Answer for ${question}`,
        basis: charts[question]?.basis || "Actual",
        response_type: "lookup",
        intent: { capability: "inventory_visibility" },
        visualization: charts[question],
        response: { response_type: "lookup", all_rows: [], display_rows: [], visualization: charts[question] },
        table: [],
        display: {},
        followups: [],
      }),
    });
  });

  await page.goto(baseUrl);
  const composer = page.getByRole("textbox", { name: /Ask about/ });
  for (const name of ["kpi", "donut", "line", "grouped_bar", "scatter", "malformed"]) {
    await composer.fill(name);
    await page.getByRole("button", { name: "Send question" }).click();
    const answer = page.locator(".assistant-content").filter({ hasText: `Answer for ${name}` });
    await answer.waitFor();
    if (name === "malformed") {
      assert.equal(await answer.locator(".visualization-card").count(), 0);
      assert.equal(await answer.getByRole("button", { name: "Supporting Visualization" }).count(), 0);
      assert.ok(await answer.getByText(`Answer for ${name}`).count());
    } else {
      const visualizationButton = answer.getByRole("button", { name: "Supporting Visualization" });
      assert.equal(await visualizationButton.getAttribute("aria-expanded"), "false");
      assert.equal(await answer.getByRole("button", { name: "Supporting Data" }).count(), 0);
      await visualizationButton.click();
      await answer.locator(".visualization-card").waitFor();
      assert.equal(await answer.locator(".visualization-card").count(), 1);
    }
  }
  assert.equal(await page.locator(".visualization-kpi").count(), 1);
  assert.equal(await page.locator(".assistant-content").filter({ hasText: "Answer for donut" }).locator(".donut-svg circle").count(), 2);
  assert.equal(await page.locator(".assistant-content").filter({ hasText: "Answer for line" }).locator("svg polyline").count(), 1);
  assert.equal(await page.locator(".assistant-content").filter({ hasText: "Answer for scatter" }).locator(".chart-axis").count(), 2);

  await page.close();
});

test("premium login uses the local background, keeps purple states, and preserves auth responses", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  let loginAttempts = 0;
  await page.route("**/auth/me", (route) => route.fulfill({ status: 401, body: "" }));
  await page.route("**/auth/login", async (route) => {
    loginAttempts += 1;
    await new Promise((resolve) => setTimeout(resolve, 120));
    await route.fulfill({
      status: loginAttempts === 1 ? 401 : 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_id: 7,
        username: "account-7",
        account_id: "account-7",
        display_name: "Test User",
        role: "LEADERSHIP",
        full_access: true,
      }),
    });
  });
  await page.route("**/conversations/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ conversations: [] }),
  }));

  await page.goto(baseUrl);
  await page.getByRole("heading", { name: "Welcome to Inventory AI Assistant" }).waitFor();
  assert.equal(
    await page.getByText("Sign in to explore inventory, sales, and operational insights.").count(),
    1,
  );
  await page.locator(".login-logo").evaluate((image) => image.decode());
  assert.equal(
    await page.locator(".login-logo").getAttribute("src"),
    "/c5i_logo_dark.png",
  );
  assert.match(
    await page.locator(".login-screen").evaluate((element) => getComputedStyle(element).backgroundImage),
    /login-geometric-background\.png/,
  );
  assert.equal(
    (await page.request.get(`${baseUrl}/login-geometric-background.png`)).status(),
    200,
  );
  assert.equal(
    await page.locator(".login-screen").evaluate((element) => getComputedStyle(element).backgroundSize),
    "cover, cover",
  );
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth === window.innerWidth),
    true,
  );

  await page.getByRole("textbox", { name: "Username" }).fill("invalid-test-account");
  await page.getByLabel("Password").fill("invalid-test-password");
  await page.getByRole("button", { name: "Sign In" }).click();
  const loadingButton = page.getByRole("button", { name: "Signing in..." });
  await loadingButton.waitFor();
  assert.equal(await loadingButton.isDisabled(), true);
  await page.getByRole("alert").waitFor();
  assert.equal(await page.getByRole("alert").innerText(), "Invalid username or password.");

  const submit = page.getByRole("button", { name: "Sign In" });
  await page.mouse.move(0, 0);
  const normalBackground = await submit.evaluate((button) => getComputedStyle(button).backgroundImage);
  await submit.hover();
  const hoverBackground = await submit.evaluate((button) => getComputedStyle(button).backgroundImage);
  assert.notEqual(hoverBackground, normalBackground);
  assert.match(hoverBackground, /rgb\(131, 57, 237\)/);

  await page.getByLabel("Password").focus();
  await page.keyboard.press("Tab");
  assert.equal(await submit.evaluate((button) => document.activeElement === button), true);
  assert.equal(await submit.evaluate((button) => getComputedStyle(button).outlineWidth), "3px");

  await submit.click();
  await page.locator(".login-card").waitFor({ state: "detached" });
  await page.locator(".sidebar").waitFor();
  assert.equal(await page.locator(".login-card").count(), 0);

  await page.close();
});
