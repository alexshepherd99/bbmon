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

// Mirrors the real dashboard.html closely enough for dashboard.js to find what
// it looks for, including the initial status text it replaces.
const dom = new JSDOM(
  `<!doctype html><html><body>
     <p class="status" id="status">Loading&hellip;</p>
     <div id="latency-chart" style="width:900px;height:480px"></div>
   </body></html>`,
  { pretendToBeVisual: true, virtualConsole }
);

global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;

const echarts = require(path.join(REPO, "bbmon/web/static/vendor/echarts.min.js"));

let chart = null;
const realInit = echarts.init;
echarts.init = function (_element, theme, _options) {
  // The only deliberate substitution: canvas needs a real browser, so this
  // renders through ECharts' server-side SVG mode instead.
  chart = realInit(null, theme, {
    renderer: "svg",
    ssr: true,
    width: 900,
    height: 480,
  });
  return chart;
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
const startedAt = Date.now();

function whenRendered() {
  return new Promise((resolve, reject) => {
    const poll = realSetInterval(() => {
      if (statusElement.textContent !== "Loading…") {
        clearInterval(poll);
        resolve();
      } else if (Date.now() - startedAt > RENDER_TIMEOUT_MS) {
        clearInterval(poll);
        reject(new Error(`dashboard.js did not finish within ${RENDER_TIMEOUT_MS}ms`));
      }
    }, 50);
  });
}

function report(svg) {
  const status = statusElement.textContent;
  console.log(`status:  ${status}`);

  if (status.startsWith("Could not load")) {
    console.error(`\nFAIL: the dashboard could not load its data from ${BASE_URL}`);
    return 1;
  }

  const series = (chart.getOption().series || []).map((s) => s.name);
  if (series.length === 0) {
    console.log(
      `\nNo data in the window yet. Start the pinger and wait for its first ` +
        `flush, then run this again.`
    );
    return 2;
  }

  const lines = [...svg.matchAll(/<path[^>]*\bd="([^"]*)"/g)]
    .map((m) => (m[1].match(/L/g) || []).length + 1)
    .filter((points) => points > 5);
  const text = [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)]
    .map((m) => m[1].trim())
    .filter(Boolean);

  console.log(`series:  ${series.join(", ")}`);
  console.log(`lines:   ${lines.length} drawn, ${lines.join("/")} points`);
  console.log(`labels:  ${text.join(" ")}`);

  const problems = [];
  if (lines.length !== series.length) {
    problems.push(`${series.length} series but ${lines.length} lines drawn`);
  }
  for (const name of series) {
    if (!text.includes(name)) problems.push(`${name} missing from the legend`);
  }
  if (!text.includes("ms")) problems.push("the y axis lost its unit");

  if (problems.length) {
    console.error(`\nFAIL: ${problems.join("; ")}`);
    return 1;
  }

  console.log(`\nOK: ${lines.length} series drawn with axes and a legend.`);
  return 0;
}

async function main() {
  await whenRendered();

  const svg = chart.renderToSVGString();
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
