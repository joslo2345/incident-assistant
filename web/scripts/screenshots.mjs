// Walks the full approval flow in a real browser and saves README screenshots.
// Needs the stack (`make up`), demo users (`make demo-users`) and the local model.
//   node web/scripts/screenshots.mjs     (reads passwords from deploy/.env)
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3001";
const OUT = new URL("../../docs/images/", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(new URL("../../deploy/.env", import.meta.url), "utf8")
    .split("\n").filter((l) => l.includes("=") && !l.startsWith("#")).map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
);
const RUN = process.env.RUN ?? "a6test-v3-residual";
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1.1 });
const page = await ctx.newPage();
page.on("dialog", (d) => d.accept(d.type() === "prompt" ? "Fan replacement scheduled for tonight" : undefined));

async function login(user, password) {
  await page.goto(BASE);
  await page.getByLabel("Username").fill(user);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByText("Incidents", { exact: true }).first().waitFor();
}

await page.goto(BASE);
await page.screenshot({ path: `${OUT}01-login.jpg`, type: "jpeg", quality: 75 });
await login("alice", env.DEMO_APPROVER_PASSWORD);

await page.goto(`${BASE}/?run=${encodeURIComponent(RUN)}`);
await page.getByRole("link", { name: /Thermal runaway/ }).first().waitFor();
await page.screenshot({ path: `${OUT}02-incidents.jpg`, type: "jpeg", quality: 75 });
log("incident list");

// A thermal incident the agent diagnosed during the eval (or INCIDENT=<uuid>).
const href = process.env.INCIDENT
  ? `/incidents/${process.env.INCIDENT}`
  : await page.getByRole("link", { name: /Thermal runaway/ }).first().getAttribute("href");
await page.goto(`${BASE}${href}`);
await page.getByText("Recommended:").waitFor();
await page.waitForTimeout(1500); // charts
await page.screenshot({ path: `${OUT}03-incident.jpg`, fullPage: true, type: "jpeg", quality: 75 });
log("incident detail", href);

// Run a fresh investigation: it files a real (pending) drain request.
log("investigating (local model, ~2 min)...");
await page.getByRole("button", { name: /Investigate (again|now)/ }).click();
await page.getByRole("button", { name: "Approve" }).waitFor({ timeout: 600_000 });
await page.screenshot({ path: `${OUT}04-pending-approval.jpg`, fullPage: true, type: "jpeg", quality: 75 });
log("pending approval");

await page.getByText("show tool-call trace").click();
await page.getByText(/model calls/).waitFor();
await page.locator("text=tool").first().click().catch(() => {});
await page.screenshot({ path: `${OUT}05-trace.jpg`, fullPage: true, type: "jpeg", quality: 75 });

await page.getByRole("button", { name: "Approve" }).click();
await page.getByText(/approved by/).waitFor();
await page.screenshot({ path: `${OUT}06-approved.jpg`, fullPage: true, type: "jpeg", quality: 75 });
log("approved");

await page.getByRole("link", { name: "Approvals audit" }).click();
await page.getByText("approved", { exact: true }).first().waitFor();
await page.screenshot({ path: `${OUT}07-audit.jpg`, type: "jpeg", quality: 75 });
log("audit");

await page.goBack();
log("asking a follow-up (~2 min)...");
await page.getByPlaceholder(/Ask about this incident/).fill("Has this node had cooling problems before, and what did we do?");
await page.getByRole("button", { name: "Ask" }).click();
await page.getByRole("button", { name: "Ask" }).waitFor({ timeout: 600_000 });
await page.getByTitle("Helpful").first().click();
await page.screenshot({ path: `${OUT}08-followup.jpg`, fullPage: true, type: "jpeg", quality: 75 });
log("follow-up answered");

await page.getByRole("link", { name: "Feedback" }).click();
await page.getByText("By diagnosed root cause").waitFor();
await page.screenshot({ path: `${OUT}09-feedback.jpg`, type: "jpeg", quality: 75 });

// A viewer sees the same incident but can't decide.
await page.getByRole("button", { name: "Log out" }).click();
await login("victor", env.DEMO_VIEWER_PASSWORD);
await page.goto(`${BASE}${href}`);
await page.getByText("Recommended:").waitFor();
const approveButtons = await page.getByRole("button", { name: "Approve" }).count();
log("viewer approve buttons:", approveButtons);
await page.screenshot({ path: `${OUT}10-viewer.jpg`, type: "jpeg", quality: 75 });

await browser.close();
if (approveButtons !== 0) throw new Error("a viewer was offered approval buttons");
log("done");
