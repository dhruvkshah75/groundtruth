// Exercise the production frontend against a running offline GroundTruth server.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright";

const base = process.env.GROUNDTRUTH_TEST_URL ?? "http://127.0.0.1:8765";
const output = process.env.GROUNDTRUTH_SIM_SCREENSHOTS;
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const session = randomUUID();
const secondSession = randomUUID();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.setDefaultTimeout(8_000);
const button = (name) => page.getByRole("button", { name, exact: true });
const clock = page.locator(".sim-clock strong");
const robot = page.locator("[data-robot]");
const stateUrl = "**/api/simulation/state";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const api = (path, options) => context.request.fetch(`${base}/api/simulation/${path}`, {
  headers: { "X-Session-ID": session }, ...options,
});

async function eventually(check) {
  const end = Date.now() + 7_000;
  while (Date.now() < end) {
    if (await check()) return;
    await delay(35);
  }
  assert.fail("Expected browser state did not appear");
}

async function post(name, endpoint, status = 200) {
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => candidate.url().endsWith(`/api/simulation/${endpoint}`)
      && candidate.request().method() === "POST"),
    button(name).click(),
  ]);
  const data = await response.json();
  assert.equal(response.status(), status, JSON.stringify(data));
  return data;
}

async function schedule(value, status = 200) {
  await page.getByLabel("Disruption", { exact: true }).selectOption(value);
  return post("Schedule disruption", "disrupt", status);
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

try {
  await page.addInitScript(({ first, second }) => {
    if (!sessionStorage.getItem("groundtruth-session-id")) sessionStorage.setItem("groundtruth-session-id", first);
    localStorage.setItem("groundtruth-theme", "light");
    localStorage.setItem("groundtruth-chats-v1", JSON.stringify([
      { id: first, title: "Warehouse demo", scenario: null, hasMessages: true },
      { id: second, title: "Second chat", scenario: null, hasMessages: true },
    ]));
  }, { first: session, second: secondSession });
  await page.goto(`${base}/simulation`);
  await page.getByRole("heading", { name: "Warehouse inspection" }).waitFor();
  await post("Load preset", "load");
  await robot.waitFor();
  assert.equal(await robot.getAttribute("transform"), "translate(100,500)");
  assert.equal(await robot.locator("path").getAttribute("transform"), "rotate(-90)");
  assert.equal(await page.locator("[data-entity=parcel_03]").count(), 1);
  const observations = page.getByRole("region", { name: "Robot observation" });
  assert.equal(await observations.getByText("Parcel 03", { exact: true }).count(), 0);
  const initialCapture = await observations.locator(".sim-time").first().innerText();

  for (const rate of ["0.25×", "1×", "0.5×"]) {
    const data = await post(rate, "control");
    assert.equal(data.snapshot_sequence, 0);
    await eventually(async () => (await button(rate).getAttribute("aria-pressed")) === "true");
    assert.equal(await clock.innerText(), "0.000 s");
    assert.equal(await observations.locator(".sim-time").first().innerText(), initialCapture);
  }

  // Key activation on a paused run advances exactly one server tick.
  await button("Play").focus();
  await page.keyboard.press("Tab");
  assert.equal(await button("Step").evaluate((el) => document.activeElement === el), true);
  const focusStyle = await button("Step").evaluate((el) => getComputedStyle(el).outlineStyle);
  assert.notEqual(focusStyle, "none");
  await page.keyboard.press("Enter");
  await eventually(async () => (await clock.innerText()) === "0.100 s");
  const previousPose = await robot.getAttribute("transform");
  await post("Play", "control");
  await eventually(async () => (await robot.getAttribute("transform")) !== previousPose);
  const paused = await post("Pause", "control");
  assert.equal(paused.playback_rate, 0.5);
  assert.ok(paused.sim_time_ms > 100);

  await page.locator(".sim-disrupt > summary").click();
  await page.locator(".sim-disrupt[open]").waitFor();
  await schedule("camera_off");
  const conflict = await schedule("camera_off", 400);
  await page.getByText(conflict.error, { exact: true }).waitFor();
  await post("Play", "control");
  await observations.getByText("Camera unavailable", { exact: true }).waitFor();
  await post("Pause", "control");
  await schedule("camera_on");
  await schedule("lidar_off");
  await schedule("yellow");
  await schedule("cart_block");
  await schedule("relocate");
  await post("Play", "control");
  await observations.getByText("LiDAR unavailable", { exact: true }).waitFor();
  await eventually(async () => Number((await clock.innerText()).split(" ")[0]) >= paused.sim_time_ms / 1000 + 2);
  await post("Pause", "control");
  await schedule("cart_clear");
  await schedule("lidar_on");
  await schedule("neutral");

  // Hold a real older GET response across reset and assert no second poll starts.
  const held = deferred();
  const release = deferred();
  let requests = 0;
  const holdSnapshot = async (route) => {
    requests++;
    if (requests !== 1) return route.continue();
    const response = await route.fetch();
    held.resolve();
    await release.promise;
    await route.fulfill({ response }).catch(() => {});
  };
  await page.route(stateUrl, holdSnapshot);
  await held.promise;
  try {
    await delay(450);
    assert.equal(requests, 1);
    await post("Reset", "control");
    await eventually(async () => (await clock.innerText()) === "0.000 s");
  } finally { release.resolve(); }
  await delay(350);
  assert.equal(await clock.innerText(), "0.000 s");
  assert.equal(await robot.getAttribute("transform"), "translate(100,500)");
  await page.unroute(stateUrl, holdSnapshot);

  // Another controller replaces the run before the browser's Step reaches the server.
  await page.route("**/api/simulation/control", async (route) => {
    const body = route.request().postDataJSON();
    const reset = await api("control", { method: "POST", data: { action: "reset", expected: body.expected } });
    assert.equal(reset.status(), 200);
    await route.continue();
  }, { times: 1 });
  const stale = await post("Step", "control", 409);
  assert.equal(stale.code, "stale_run");
  await page.getByText(stale.error, { exact: true }).waitFor();

  await post("Play", "control");
  const offline = (route) => route.abort("internetdisconnected");
  await page.route(stateUrl, offline);
  await page.getByText("Backend disconnected", { exact: true }).waitFor();
  await page.getByText("Stale snapshot", { exact: true }).waitFor();
  const frozenPose = await robot.getAttribute("transform");
  await delay(400);
  assert.equal(await robot.getAttribute("transform"), frozenPose);
  await page.unroute(stateUrl, offline);
  await page.getByText("Running", { exact: true }).waitFor();
  await eventually(async () => (await robot.getAttribute("transform")) !== frozenPose);
  await post("Pause", "control");

  // Simulate browser lifecycle visibility while preserving the real connection.
  let polls = 0;
  const onRequest = (request) => { if (request.url().endsWith("/api/simulation/state")) polls++; };
  page.on("request", onRequest);
  const visibility = (hidden) => page.evaluate((value) => {
    Object.defineProperty(document, "hidden", { configurable: true, value });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);
  await visibility(true);
  await page.getByText("Updates suspended", { exact: true }).waitFor();
  const suspendedCount = polls;
  await delay(550);
  assert.equal(polls, suspendedCount);
  await visibility(false);
  await page.getByText("Paused", { exact: true }).waitFor();
  assert.ok(polls > suspendedCount);

  // Layout, contrast, reduced motion and real-backend screenshots.
  const contrast = await page.locator(".sim-heading h2").evaluate((el) => {
    const luminance = (rgb) => rgb.match(/\d+/g).slice(0, 3).map(Number).map((v) => {
      const c = v / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
    const values = [luminance(getComputedStyle(el).color),
      luminance(getComputedStyle(el.closest(".simulation-workspace")).backgroundColor)].sort((a, b) => b - a);
    return (values[0] + 0.05) / (values[1] + 0.05);
  });
  assert.ok(contrast >= 4.5);
  if (output) {
    await mkdir(output, { recursive: true });
    await page.screenshot({ path: join(output, "simulation-workspace-desktop.png") });
  }
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const overflow = await page.locator(".simulation-workspace").evaluate((el) => el.scrollWidth > el.clientWidth);
    assert.equal(overflow, false, `Horizontal overflow at ${width}px`);
    assert.ok((await page.locator(".warehouse-map").boundingBox()).width > 240);
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(await button("Play").evaluate((el) => getComputedStyle(el).transitionDuration), "0s");
  await button("Switch to dark theme").click();
  if (output) await page.screenshot({ path: join(output, "simulation-workspace-mobile.png") });

  await page.setViewportSize({ width: 1440, height: 900 });
  const inFlight = deferred();
  const endRequest = deferred();
  const holdUntilNavigation = async (route) => {
    inFlight.resolve();
    await endRequest.promise;
    await route.abort().catch(() => {});
  };
  await page.route(stateUrl, holdUntilNavigation, { times: 1 });
  await inFlight.promise;
  try {
    await Promise.all([
      page.waitForEvent("requestfailed", (request) => request.url().endsWith("/api/simulation/state")),
      button("Chat").click(),
    ]);
  } finally { endRequest.resolve(); }
  const unmountedCount = polls;
  await delay(550);
  assert.equal(polls, unmountedCount);
  await button("System inspector").click();
  await page.getByRole("heading", { name: "System inspector", exact: true }).first().waitFor();
  await button("Belief graph").click();
  await page.getByRole("heading", { name: "Belief graph", exact: true }).first().waitFor();
  await button("Chat").click();
  await button("Second chat").click();
  await page.getByRole("heading", { name: "Second chat", exact: true }).waitFor();
  await button("Simulation").click();
  await page.getByText("No run loaded", { exact: true }).waitFor();
  assert.equal(await robot.count(), 0);
  await page.reload();
  await page.getByRole("heading", { name: "Warehouse inspection" }).waitFor();
  await page.getByText("Session · Second chat", { exact: true }).waitFor();

  console.log("Simulation browser checks passed on the real backend at 1440, 1024, 768 and 390px.");
} finally {
  await browser.close();
}
