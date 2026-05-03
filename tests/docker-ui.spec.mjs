import { test, expect } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const baseURL = process.env.BASE_URL ?? "http://127.0.0.1:18080";
const adminEmail = process.env.UI_TEST_ADMIN_EMAIL ?? "admin@example.com";
const adminPassword = process.env.UI_TEST_ADMIN_PASSWORD ?? "password123";

test.use({ baseURL });

test("docker image serves core authenticated screens", async ({ page, request }) => {
  await expect
    .poll(async () => {
      try {
        const response = await request.get("/healthz");
        return response.ok() ? await response.json() : null;
      } catch {
        return null;
      }
    }, { timeout: 30_000 })
    .toEqual({ status: "ok" });

  const stylesheetResponse = await request.get("/static/styles.css");
  expect(stylesheetResponse.status()).toBe(200);
  await expect(stylesheetResponse.text()).resolves.toContain("font-family");

  await page.goto("/");
  await expect(page).toHaveTitle(/Create Admin Account|Schwinn Welcome/);
  await expect(page.locator('link[rel="stylesheet"]')).toHaveAttribute("href", "/static/styles.css");
  await expect(page.locator("body")).toHaveCSS("font-family", /Inter/);

  if (await page.getByRole("heading", { name: "Create Admin Account" }).isVisible()) {
    await page.getByLabel("First Name").fill("Admin");
    await page.getByLabel("Last Name").fill("User");
    await page.getByRole("textbox", { name: "Admin Email" }).fill(adminEmail);
    await page.getByLabel("Admin email address has been verified").check();
    await page.getByLabel("Password", { exact: true }).fill(adminPassword);
    await page.getByLabel("Confirm Password").fill(adminPassword);
    await expect(page.getByText("Passwords match")).toBeVisible();
    await page.getByRole("button", { name: "Create Admin" }).click();
    await expect(page.getByRole("heading", { name: "Workout Performance" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Admin Settings" })).toBeVisible();
  }

  await page.goto("/");
  await expect(page.getByLabel("Account menu")).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign out" })).toBeVisible();
  await page.getByLabel("Account menu").click();
  await expect(page.getByRole("link", { name: "Profile & Settings" })).toBeVisible();
  await page.getByRole("link", { name: "Profile & Settings" }).click();
  await expect(page.getByRole("heading", { name: "Profile", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Password" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Profile Image" })).toBeVisible();
  await page.goto("/");
  await page.locator("details.nav-menu summary").click();
  await expect(page.getByRole("link", { name: "Users" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Welcome Back!" })).toBeVisible();
  await expect(page.getByText("No workouts have been loaded yet.")).toBeVisible();

  await page.goto("/workout-performance");
  await expect(page.getByRole("button", { name: "Refresh Dashboard" })).toBeVisible();
  await expect(page.getByText("Records in selected date range:")).toBeVisible();
  await expect(page.getByText("No historical data loaded for the selected date range.")).toBeVisible();

  await page.goto("/workout-details");
  await expect(page.getByRole("heading", { name: "Workout Details" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh Details" })).toBeVisible();

  const historyDir = mkdtempSync(join(tmpdir(), "schwinn-history-"));
  const historyCsvPath = join(historyDir, "Workout_History.csv");
  writeFileSync(
    historyCsvPath,
    [
      "Workout_Date,Distance,Avg_Speed,Workout_Time,Total_Calories,Heart_Rate,RPM,Level",
      "2026-01-04,3.5,12.2,30,210,128,72,4",
      "2026-01-11,4.5,13.2,40,260,132,76,5",
      "2026-02-01,6.5,14.4,50,320,136,80,6",
      "2026-03-01,8.0,15.0,60,410,142,84,7",
    ].join("\n"),
  );
  await page.goto("/upload-history");
  await page.getByLabel("Select Historical CSV").setInputFiles(historyCsvPath);
  await page.getByRole("button", { name: "Import Historical CSV" }).click();
  await expect(page.getByText("Uploaded Workout_History.csv")).toBeVisible();

  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 820, height: 1100 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    for (const path of ["/workout-performance", "/workout-details"]) {
      await page.goto(path);
      await expect(page.locator(".hero")).toBeVisible();
      await expect(page.locator(".plotly-graph-div").first()).toBeVisible();
      const hasPageOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
      expect(hasPageOverflow).toBe(false);
    }
  }

  await page.goto("/upload-workout");
  await expect(page.getByRole("heading", { name: "Enter New Workout" })).toBeVisible();
  await expect(page.getByLabel("Select Workout File")).toBeVisible();

  await page.goto("/upload-history");
  await expect(page.getByRole("heading", { name: "Load Historical Data" })).toBeVisible();
  await expect(page.getByLabel("Select Historical CSV")).toBeVisible();

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Admin Settings" })).toBeVisible();
  await expect(page.getByLabel("New User Registration")).toBeVisible();
});
