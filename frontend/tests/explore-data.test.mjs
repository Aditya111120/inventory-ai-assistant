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
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        narrative: noRows ? "No matching products were found." : "Product One has the highest inventory.",
        basis: "Actual",
        response_type: "lookup",
        intent: { capability: "inventory_visibility" },
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
        },
        table: [],
        display: {},
        followups: [],
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
  assert.equal(await answer.locator(".result-table").count(), 0);
  assert.equal(await answer.getByText("How this answer was produced").count(), 0);
  assert.equal(await answer.locator(".response-evidence-row .basis-pill").count(), 1);
  assert.equal(await answer.locator(".response-evidence-row .supporting-data-button").count(), 1);

  await supportingButton.click();
  assert.equal(await supportingButton.getAttribute("aria-expanded"), "true");
  assert.equal(await answer.locator(".result-table tbody tr").count(), 1);
  assert.ok(await answer.getByText("Product One").count());
  assert.equal(await answer.getByText("Product Two").count(), 0);
  await answer.getByRole("button", { name: /View 2 returned rows \(3 matching\)/ }).click();
  assert.equal(await answer.locator(".result-table tbody tr").count(), 2);
  assert.equal(await answer.getByText("Product Two").count(), 1);
  await supportingButton.click();
  assert.equal(await answer.locator(".result-table").count(), 0);

  await composer.fill("Show an empty analytical result");
  await page.getByRole("button", { name: "Send question" }).click();
  const emptyAnswer = page.locator(".assistant-content").filter({ hasText: "No matching products were found." });
  const emptySupportingButton = emptyAnswer.getByRole("button", { name: /Supporting Data/ });
  await emptySupportingButton.waitFor();
  await emptySupportingButton.click();
  await emptyAnswer.getByText("No supporting records available").waitFor();
  assert.equal(await emptyAnswer.locator(".result-table").count(), 0);

  await page.close();
});
