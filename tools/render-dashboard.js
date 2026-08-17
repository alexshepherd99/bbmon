#!/usr/bin/env node
/**
 * Renders the dashboard headlessly, so a chart change can be checked without a
 * browser to hand.
 *
 * It runs the shipped `dashboard.js` against the vendored ECharts bundle and a
 * live `/api/ping`, then asserts the chart actually drew: one line per target,
 * with points, axes and a legend. Written after the dashboard's first real
 * render turned up a series that was almost invisible — the kind of defect the
 * Python tests cannot see.
 *
 * Usage, with `python -m bbmon.web` already running:
 *
 *     node tools/render-dashboard.js [base-url]
 *
 * Requires jsdom, which is deliberately NOT declared in this repo. There is no
 * package.json here on purpose: the project vendors its front-end assets so
 * that nothing in it ever runs `npm install`, and this development-only tool
 * does not get to reintroduce that. Install it outside the repo and point
 * NODE_PATH at it:
 *
 *     mkdir -p ~/.bbmon-tools && (cd ~/.bbmon-tools && npm install jsdom)
 *     NODE_PATH=~/.bbmon-tools/node_modules node tools/render-dashboard.js
 *
 * Installing `@resvg/resvg-js` there as well gets you a PNG to look at. It is
 * kept optional because it ships a prebuilt native binary, and the structural
 * checks below do not need one.
 *
 * Exit codes: 0 drew as expected, 1 failed to draw, 2 no data in the window.
 *
 * What this does NOT cover: the canvas renderer (this uses ECharts' SVG
 * server-side mode), the poll timer, resize handling, and CSS layout. Those
 * need a real browser.
 */

"use strict";

const fs = require("fs");
const path = require("path");

const REPO = path.join(__dirname, "..");
const BASE_URL = process.argv[2] || "http://127.0.0.1:8080";
const OUT_DIR = path.join(REPO, "var");
const RENDER_TIMEOUT_MS = 10000;

function requireOptional(name) {
  try {
    return require(name);
  } catch {
    return null;
  }
}

const jsdomModule = requireOptional("jsdom");
if (!jsdomModule) {
  console.error(
    "jsdom is not available. See the header of this file — install it outside " +
      "the repo and set NODE_PATH, rather than adding a package.json here."
  );
  process.exit(1);
}

const { JSDOM, VirtualConsole } = jsdomModule;

// ECharts measures text through a canvas, which jsdom does not implement and
// loudly says so once per measurement. It falls back correctly, so the noise is
// dropped while real page output still comes through.
const virtualConsole = new VirtualConsole();
virtualConsole.forwardTo(console, { jsdomErrors: "none" });

// Deliberately not the real page's placeholder text. "Waiting for the first
// speed test" is what dashboard.js itself writes when no test has ever run, so
// reusing it here would make "the fetch has not finished" and "there is no data"
// indistinguishable, and the panel would be read before it had loaded.
const PENDING = "pending…";

// Mirrors the real dashboard.html closely enough for dashboard.js to find what
// it looks for, including the initial status text it replaces. Every element
// the page script reaches for has to be here: it addresses them directly and
// does not guard against nulls, because on the real page they always exist.
const dom = new JSDOM(
  `<!doctype html><html><body>
     <p class="status" id="status">Loading&hellip;</p>
     <section id="speedtest-panel">
       <span id="speedtest-download">&mdash;</span>
       <span id="speedtest-upload">&mdash;</span>
       <span id="speedtest-ping">&mdash;</span>
       <p id="speedtest-meta">${PENDING}</p>
     </section>
     <div id="latency-chart" style="width:900px;height:480px"></div>
     <p id="hourly-note">${PENDING}</p>
     <div id="hourly-chart" style="width:900px;height:480px"></div>
     <div id="history-range">
       <button type="button" data-days="1">24h</button>
       <button type="button" data-days="7" class="selected">7d</button>
       <button type="button" data-days="30">30d</button>
     </div>
     <p id="history-note">${PENDING}</p>
     <div id="history-chart" style="width:900px;height:480px"></div>
     <table><tbody id="restart-rows"></tbody></table>
     <p id="restart-note">${PENDING}</p>
     <input type="checkbox" id="hide-expected">
   </body></html>`,
  { pretendToBeVisual: true, virtualConsole }
);

global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;

const echarts = require(path.join(REPO, "bbmon/web/static/vendor/echarts.min.js"));

// The page now builds three charts, so they are kept by the id of the element
// each was asked to render into rather than as a single handle.
const charts = new Map();
const realInit = echarts.init;
echarts.init = function (element, theme, _options) {
  // The only deliberate substitution: canvas needs a real browser, so this
  // renders through ECharts' server-side SVG mode instead.
  const created = realInit(null, theme, {
    renderer: "svg",
    ssr: true,
    width: 900,
    height: 480,
  });
  charts.set(element ? element.id : `chart-${charts.size}`, created);
  return created;
};
global.echarts = echarts;

// Left un-stubbed apart from resolving the page-relative URL, so this exercises
// a real request against the running app rather than a canned fixture.
const realFetch = global.fetch;
global.fetch = (url, options) => realFetch(new URL(url, BASE_URL), options);

// dashboard.js's own poll would keep the process alive with nothing further to
// prove, so it is stubbed out — but this harness still needs the real timer for
// its own waiting, hence the handle kept here.
const realSetInterval = global.setInterval;
global.setInterval = () => 0;

require(path.join(REPO, "bbmon/web/static/dashboard.js"));

const statusElement = document.getElementById("status");
const speedtestMetaElement = document.getElementById("speedtest-meta");
const hourlyNoteElement = document.getElementById("hourly-note");
const historyNoteElement = document.getElementById("history-note");
const restartNoteElement = document.getElementById("restart-note");
const startedAt = Date.now();

function whenRendered() {
  return new Promise((resolve, reject) => {
    const poll = realSetInterval(() => {
      // Every panel is polled on its own timer, so all of them have to have
      // written before anything is read — otherwise a panel is judged on
      // whatever the DOM happened to hold when the fastest one finished.
      const done =
        statusElement.textContent !== "Loading…" &&
        speedtestMetaElement.textContent !== PENDING &&
        hourlyNoteElement.textContent !== PENDING &&
        historyNoteElement.textContent !== PENDING &&
        restartNoteElement.textContent !== PENDING;
      if (done) {
        clearInterval(poll);
        resolve();
      } else if (Date.now() - startedAt > RENDER_TIMEOUT_MS) {
        clearInterval(poll);
        reject(new Error(`dashboard.js did not finish within ${RENDER_TIMEOUT_MS}ms`));
      }
    }, 50);
  });
}

// The speed test panel is ordinary DOM rather than an ECharts canvas, so it
// never reaches the SVG. It is checked here instead, because otherwise nothing
// short of a real browser exercises this code path at all.
function speedtestProblems() {
  const meta = speedtestMetaElement.textContent;
  const download = document.getElementById("speedtest-download").textContent;
  console.log(`speedtest: ${download} Mbps down — ${meta}`);

  if (meta.startsWith("Could not load")) {
    return ["the speed test panel could not load its data"];
  }
  if (meta.startsWith("Waiting for the first")) {
    // Not a failure: a database with no speed test in it is the normal state
    // until the service has run once.
    return [];
  }
  if (meta.startsWith("Last attempt failed")) {
    // Also not a failure of the page. A failed run is meant to blank the
    // readings rather than leave the previous run's figures reading as
    // current, so an empty download here is the correct rendering.
    return download === "—"
      ? []
      : ["a failed speed test left stale readings on screen"];
  }
  if (download === "—") {
    return ["a successful speed test left the download reading blank"];
  }
  return [];
}

function textIn(svg) {
  return [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)]
    .map((m) => m[1].trim())
    .filter(Boolean);
}

function seriesNames(chart) {
  return (chart.getOption().series || []).map((s) => s.name);
}

/** The live latency chart: one line per target, with a legend and a unit. */
function latencyProblems(svg) {
  const series = seriesNames(charts.get("latency-chart"));
  const lines = [...svg.matchAll(/<path[^>]*\bd="([^"]*)"/g)]
    .map((m) => (m[1].match(/L/g) || []).length + 1)
    .filter((points) => points > 5);
  const text = textIn(svg);

  console.log(`latency: ${series.join(", ")}`);
  console.log(`lines:   ${lines.length} drawn, ${lines.join("/")} points`);

  const problems = [];
  if (lines.length !== series.length) {
    problems.push(`${series.length} series but ${lines.length} lines drawn`);
  }
  for (const name of series) {
    if (!text.includes(name)) problems.push(`${name} missing from the legend`);
  }
  if (!text.includes("ms")) problems.push("the latency chart lost its y axis unit");
  return problems;
}

/**
 * The hourly box plot. Boxes are drawn as <path> elements like the lines are,
 * so this checks the series and the hour labels rather than counting shapes:
 * what would actually go wrong is boxes landing under the wrong hour, or the
 * targets overlapping into one column.
 */
function hourlyProblems() {
  const note = hourlyNoteElement.textContent;
  console.log(`hourly:  ${note}`);

  if (note.startsWith("Could not load")) {
    return ["the hourly summary could not load its data"];
  }
  if (note.startsWith("No pings recorded")) {
    return [];
  }

  const chart = charts.get("hourly-chart");
  const option = chart.getOption();
  const hours = (option.xAxis || [])[0]?.data || [];
  const series = option.series || [];
  const svg = chart.renderToSVGString();
  const labels = textIn(svg);

  console.log(`  boxes: ${series.length} targets over ${hours.length} hours`);

  const problems = [];
  if (series.length === 0) problems.push("the box plot drew no series");
  for (const one of series) {
    if (one.type !== "boxplot") problems.push(`${one.name} is a ${one.type}, not a boxplot`);
    if (one.data.length !== hours.length) {
      problems.push(
        `${one.name} has ${one.data.length} boxes for ${hours.length} hours`
      );
    }
  }
  if (hours.length && !labels.includes(hours[0])) {
    problems.push(`the hour axis is missing its first label ${hours[0]}`);
  }
  return problems;
}

/** The speed test history: three named series against two y axes. */
function historyProblems() {
  const note = historyNoteElement.textContent;
  console.log(`history: ${note}`);

  if (note.startsWith("Could not load")) {
    return ["the speed test history could not load its data"];
  }
  if (note.startsWith("No speed tests")) {
    return [];
  }

  const chart = charts.get("history-chart");
  const names = seriesNames(chart);
  const labels = textIn(chart.renderToSVGString());

  const problems = [];
  for (const expected of ["Download", "Upload", "Latency"]) {
    if (!names.includes(expected)) problems.push(`${expected} missing from the history`);
    if (!labels.includes(expected)) problems.push(`${expected} missing from its legend`);
  }
  // Throughput and latency share no scale, so losing the second axis would
  // flatten latency against the line speed rather than fail visibly.
  for (const unit of ["Mbps", "ms"]) {
    if (!labels.includes(unit)) problems.push(`the history lost its ${unit} axis`);
  }
  return problems;
}

/** The restart table: rows rendered, or a note saying why there are none. */
function restartProblems() {
  const note = restartNoteElement.textContent;
  const rows = document.getElementById("restart-rows").children.length;
  console.log(`restarts: ${rows} rows — ${note}`);

  if (note.startsWith("Could not load")) {
    return ["the restart list could not load its data"];
  }
  if (note.startsWith("No restarts") || note.startsWith("No unexpected")) {
    return rows === 0 ? [] : ["the restart list says it is empty but drew rows"];
  }
  return rows === 0 ? ["the restart list reported rows but drew none"] : [];
}

function report(svg) {
  const status = statusElement.textContent;
  console.log(`status:  ${status}`);

  if (status.startsWith("Could not load")) {
    console.error(`\nFAIL: the dashboard could not load its data from ${BASE_URL}`);
    return 1;
  }

  if (seriesNames(charts.get("latency-chart")).length === 0) {
    console.log(
      `\nNo data in the window yet. Start the pinger and wait for its first ` +
        `flush, then run this again.`
    );
    return 2;
  }

  const problems = [
    ...latencyProblems(svg),
    ...hourlyProblems(),
    ...historyProblems(),
    ...restartProblems(),
    ...speedtestProblems(),
  ];

  if (problems.length) {
    console.error(`\nFAIL: ${problems.join("; ")}`);
    return 1;
  }

  console.log(`\nOK: every panel drew.`);
  return 0;
}

async function main() {
  await whenRendered();

  const svg = charts.get("latency-chart").renderToSVGString();
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const svgPath = path.join(OUT_DIR, "dashboard.svg");
  fs.writeFileSync(svgPath, svg);
  console.log(`wrote:   ${path.relative(REPO, svgPath)}`);

  const resvg = requireOptional("@resvg/resvg-js");
  if (resvg) {
    const pngPath = path.join(OUT_DIR, "dashboard.png");
    const rendered = new resvg.Resvg(svg, {
      background: "#182029",
      fitTo: { mode: "width", value: 1000 },
    }).render();
    fs.writeFileSync(pngPath, rendered.asPng());
    console.log(`wrote:   ${path.relative(REPO, pngPath)}`);
  } else {
    console.log("note:    install @resvg/resvg-js for a PNG to look at");
  }

  return report(svg);
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(`FAIL: ${error.message}`);
    process.exit(1);
  }
);
