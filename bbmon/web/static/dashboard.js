"use strict";

// Polls the ping API and redraws the latency chart in place. Requirement 7
// asks for updates without a full page reload.

const POLL_INTERVAL_MS = 5000;

// Slots 1-3 of a categorical palette stepped for a dark surface, kept in this
// fixed order so a target always keeps its colour even when others drop out.
// Validated for colour-vision deficiency separation against this background;
// ECharts' own default palette is stepped for a light surface and leaves the
// third series washed out here.
const SERIES_COLOURS = ["#3987e5", "#d95926", "#199e70"];

const AXIS_INK = "#8fa3b5";
const GRID_INK = "#24303c";

const chart = echarts.init(document.getElementById("latency-chart"), null, {
  renderer: "canvas",
});
const status = document.getElementById("status");

window.addEventListener("resize", () => chart.resize());

function baseOptions() {
  return {
    color: SERIES_COLOURS,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", label: { backgroundColor: GRID_INK } },
    },
    // The legend carries identity, so the series are never told apart by
    // colour alone.
    legend: { top: 0, textStyle: { color: AXIS_INK } },
    grid: { left: 48, right: 16, top: 36, bottom: 32 },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: GRID_INK } },
      axisLabel: { color: AXIS_INK },
    },
    yAxis: {
      type: "value",
      name: "ms",
      nameTextStyle: { color: AXIS_INK },
      axisLabel: { color: AXIS_INK },
      splitLine: { lineStyle: { color: GRID_INK } },
    },
  };
}

function seriesFor(targets) {
  return Object.keys(targets)
    .sort()
    .map((target) => ({
      name: target,
      type: "line",
      showSymbol: false,
      lineStyle: { width: 2 },
      // Two hours at a five-second interval is ~1440 points per target;
      // downsampling keeps the redraw cheap on a phone without changing the
      // shape of the line.
      sampling: "lttb",
      // A failed ping arrives as null, which leaves a visible gap rather than
      // a straight line drawn across the outage.
      connectNulls: false,
      data: targets[target],
    }));
}

function describe(count, generatedAt) {
  const time = new Date(generatedAt).toLocaleTimeString();
  return count === 0
    ? `No pings recorded yet — updated ${time}`
    : `${count} points — updated ${time}`;
}

// The names of the series currently on the chart, in order. Replacing the
// series makes ECharts treat them as new and replay the entry animation, which
// at a five-second poll meant the lines redrew left to right every five
// seconds. Replacement is only needed when a target actually appears or
// disappears; otherwise the new data is merged into the existing series, which
// transitions instead of restarting.
let drawnTargets = [];

function sameSeriesAsDrawn(names) {
  return (
    names.length === drawnTargets.length &&
    names.every((name, index) => name === drawnTargets[index])
  );
}

async function refresh() {
  try {
    const response = await fetch("/api/ping");
    if (!response.ok) {
      throw new Error(`the server returned ${response.status}`);
    }
    const body = await response.json();
    const series = seriesFor(body.targets);
    const points = series.reduce((total, s) => total + s.data.length, 0);
    const names = series.map((s) => s.name);

    if (sameSeriesAsDrawn(names)) {
      chart.setOption({ series: series.map((s) => ({ data: s.data })) });
    } else {
      chart.setOption({ ...baseOptions(), series }, { replaceMerge: ["series"] });
      drawnTargets = names;
    }

    status.textContent = describe(points, body.generated_at);
    status.classList.remove("error");
  } catch (error) {
    status.textContent = `Could not load ping data: ${error.message}`;
    status.classList.add("error");
  }
}

// The speed test runs every few hours, so polling it at the ping rate would be
// thousands of pointless queries a day on a Pi 3. Slow enough to be cheap,
// often enough that a finished test appears without a page reload.
const SPEEDTEST_POLL_INTERVAL_MS = 60000;

const speedtestFields = {
  download: document.getElementById("speedtest-download"),
  upload: document.getElementById("speedtest-upload"),
  ping: document.getElementById("speedtest-ping"),
};
const speedtestMeta = document.getElementById("speedtest-meta");

function formatReading(value) {
  return value === null || value === undefined ? "—" : value.toFixed(1);
}

function describeSpeedtest(body) {
  const when = new Date(body.timestamp).toLocaleString();
  if (!body.success) {
    return `Last attempt failed at ${when} — the line or the test server was unreachable`;
  }
  const where = [body.isp, body.server].filter(Boolean).join(" · ");
  return where ? `${when} · ${where}` : when;
}

async function refreshSpeedtest() {
  try {
    const response = await fetch("/api/speedtest/latest");
    if (!response.ok) {
      throw new Error(`the server returned ${response.status}`);
    }
    const body = await response.json();

    if (body.result === null) {
      speedtestMeta.textContent = "Waiting for the first speed test…";
      speedtestMeta.classList.remove("error");
      return;
    }

    // A failed run clears the numbers rather than leaving the previous run's
    // figures on screen, where they would read as current.
    speedtestFields.download.textContent = formatReading(body.download_mbps);
    speedtestFields.upload.textContent = formatReading(body.upload_mbps);
    speedtestFields.ping.textContent = formatReading(body.ping_ms);

    speedtestMeta.textContent = describeSpeedtest(body);
    speedtestMeta.classList.toggle("error", !body.success);
  } catch (error) {
    speedtestMeta.textContent = `Could not load the speed test: ${error.message}`;
    speedtestMeta.classList.add("error");
  }
}

// The remaining three panels all move far more slowly than the live latency
// chart — an hourly box changes once an hour, a speed test runs every few
// hours, and a restart is a rare event. Polling them at the ping rate would be
// thousands of pointless queries a day on a Pi 3.
const SLOW_POLL_INTERVAL_MS = 300000;

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`the server returned ${response.status}`);
  }
  return response.json();
}

function setNote(element, message, isError) {
  element.textContent = message;
  element.classList.toggle("error", Boolean(isError));
}

// --- ping latency, boxed by hour -------------------------------------------

const hourlyChart = echarts.init(document.getElementById("hourly-chart"), null, {
  renderer: "canvas",
});
const hourlyNote = document.getElementById("hourly-note");

function hourLabel(iso) {
  const when = new Date(iso);
  return when.getHours().toString().padStart(2, "0") + ":00";
}

function boxplotTooltip(params) {
  // params.value for a boxplot is [categoryIndex, low, q1, median, q3, high],
  // so the five statistics start at index 1 rather than 0.
  const [, low, q1, median, q3, high] = params.value;
  const count = params.data.count;
  return (
    `${params.name} &middot; ${params.seriesName}<br>` +
    `max ${high.toFixed(1)} ms<br>` +
    `upper quartile ${q3.toFixed(1)} ms<br>` +
    `median ${median.toFixed(1)} ms<br>` +
    `lower quartile ${q1.toFixed(1)} ms<br>` +
    `min ${low.toFixed(1)} ms<br>` +
    `from ${count} ping${count === 1 ? "" : "s"}`
  );
}

function hourlyOptions(hours, targets) {
  return {
    color: SERIES_COLOURS,
    tooltip: { trigger: "item", formatter: boxplotTooltip },
    legend: { top: 0, textStyle: { color: AXIS_INK } },
    grid: { left: 48, right: 16, top: 36, bottom: 40 },
    xAxis: {
      type: "category",
      data: hours,
      axisLine: { lineStyle: { color: GRID_INK } },
      axisLabel: { color: AXIS_INK },
      // 24 hours of labels will not fit on a phone, so ECharts drops every
      // other one rather than overlapping them.
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: "value",
      name: "ms",
      nameTextStyle: { color: AXIS_INK },
      axisLabel: { color: AXIS_INK },
      splitLine: { lineStyle: { color: GRID_INK } },
    },
    series: targets,
  };
}

function boxesByTarget(buckets, hours) {
  // Every target gets an entry for every hour on the axis, so the boxes line
  // up under the right label even when one target has a gap the others do not.
  const positions = new Map(hours.map((hour, index) => [hour, index]));
  const byTarget = new Map();

  for (const bucket of buckets) {
    if (!byTarget.has(bucket.target)) {
      byTarget.set(bucket.target, new Array(hours.length).fill(null));
    }
    byTarget.get(bucket.target)[positions.get(bucket.hour)] = {
      value: [bucket.low, bucket.q1, bucket.median, bucket.q3, bucket.high],
      count: bucket.count,
    };
  }

  return [...byTarget.keys()].sort().map((target) => ({
    name: target,
    type: "boxplot",
    data: byTarget.get(target),
  }));
}

async function refreshHourly() {
  try {
    const body = await fetchJson("/api/ping/hourly");

    if (body.buckets.length === 0) {
      setNote(hourlyNote, "No pings recorded in the last 24 hours yet", false);
      hourlyChart.clear();
      return;
    }

    const hours = [...new Set(body.buckets.map((bucket) => bucket.hour))];
    const series = boxesByTarget(body.buckets, hours);

    hourlyChart.setOption(
      hourlyOptions(hours.map(hourLabel), series),
      { replaceMerge: ["series", "xAxis"] }
    );
    setNote(hourlyNote, `${hours.length} of the last ${body.hours} hours`, false);
  } catch (error) {
    setNote(hourlyNote, `Could not load the hourly summary: ${error.message}`, true);
  }
}

// --- speed test history ----------------------------------------------------

const historyChart = echarts.init(document.getElementById("history-chart"), null, {
  renderer: "canvas",
});
const historyNote = document.getElementById("history-note");
const historyRange = document.getElementById("history-range");

let historyDays = 7;

function historyOptions(series) {
  return {
    color: SERIES_COLOURS,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", label: { backgroundColor: GRID_INK } },
    },
    legend: { top: 0, textStyle: { color: AXIS_INK } },
    grid: { left: 52, right: 52, top: 36, bottom: 32 },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: GRID_INK } },
      axisLabel: { color: AXIS_INK },
    },
    // Throughput and latency share no scale, so latency gets its own axis on
    // the right rather than being flattened against a line-speed range.
    yAxis: [
      {
        type: "value",
        name: "Mbps",
        nameTextStyle: { color: AXIS_INK },
        axisLabel: { color: AXIS_INK },
        splitLine: { lineStyle: { color: GRID_INK } },
      },
      {
        type: "value",
        name: "ms",
        nameTextStyle: { color: AXIS_INK },
        axisLabel: { color: AXIS_INK },
        splitLine: { show: false },
      },
    ],
    series,
  };
}

function historySeries(results) {
  const points = (field) =>
    results.map((row) => [new Date(row.timestamp).getTime(), row[field]]);

  return [
    { name: "Download", type: "line", yAxisIndex: 0, data: points("download_mbps") },
    { name: "Upload", type: "line", yAxisIndex: 0, data: points("upload_mbps") },
    { name: "Latency", type: "line", yAxisIndex: 1, data: points("ping_ms") },
  ].map((series) => ({
    ...series,
    showSymbol: true,
    symbolSize: 5,
    lineStyle: { width: 2 },
    // A failed run has no readings, so it arrives as a null and leaves a gap.
    // Joining across it would draw a line through an outage.
    connectNulls: false,
  }));
}

async function refreshHistory() {
  try {
    const body = await fetchJson(`/api/speedtest/history?days=${historyDays}`);

    if (body.results.length === 0) {
      setNote(historyNote, `No speed tests in the last ${body.days} days`, false);
      historyChart.clear();
      return;
    }

    historyChart.setOption(historyOptions(historySeries(body.results)), {
      replaceMerge: ["series"],
    });

    const failures = body.results.filter((row) => !row.success).length;
    const failureNote = failures === 0 ? "" : `, ${failures} failed`;
    setNote(
      historyNote,
      `${body.results.length} tests over ${body.days} days${failureNote}`,
      false
    );
  } catch (error) {
    setNote(historyNote, `Could not load the history: ${error.message}`, true);
  }
}

historyRange.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-days]");
  if (!button) return;

  historyDays = Number(button.dataset.days);
  for (const other of historyRange.querySelectorAll("button")) {
    const selected = other === button;
    other.classList.toggle("selected", selected);
    other.setAttribute("aria-pressed", String(selected));
  }
  refreshHistory();
});

// --- restarts --------------------------------------------------------------

const restartRows = document.getElementById("restart-rows");
const restartNote = document.getElementById("restart-note");
const hideExpected = document.getElementById("hide-expected");

function restartRow(restart) {
  const row = document.createElement("tr");
  const cells = [
    new Date(restart.timestamp).toLocaleString(),
    restart.expected ? "Scheduled" : "Unexpected",
    restart.reason || "—",
  ];

  for (const [index, text] of cells.entries()) {
    const cell = document.createElement("td");
    // textContent, not innerHTML: the reason is stored data, and this is the
    // one place on the page that renders it.
    cell.textContent = text;
    if (index === 1 && !restart.expected) cell.classList.add("unexpected");
    row.appendChild(cell);
  }
  return row;
}

async function refreshRestarts() {
  try {
    const include = hideExpected.checked ? "false" : "true";
    const body = await fetchJson(`/api/restarts?include_expected=${include}`);

    restartRows.replaceChildren(...body.restarts.map(restartRow));

    if (body.restarts.length === 0) {
      setNote(
        restartNote,
        hideExpected.checked
          ? "No unexpected restarts recorded"
          : "No restarts recorded yet",
        false
      );
      return;
    }
    setNote(restartNote, `Showing the last ${body.restarts.length}`, false);
  } catch (error) {
    setNote(restartNote, `Could not load the restarts: ${error.message}`, true);
  }
}

hideExpected.addEventListener("change", refreshRestarts);

// --- polling ---------------------------------------------------------------

window.addEventListener("resize", () => {
  hourlyChart.resize();
  historyChart.resize();
});

refresh();
setInterval(refresh, POLL_INTERVAL_MS);

refreshSpeedtest();
setInterval(refreshSpeedtest, SPEEDTEST_POLL_INTERVAL_MS);

refreshHourly();
setInterval(refreshHourly, SLOW_POLL_INTERVAL_MS);

refreshHistory();
setInterval(refreshHistory, SLOW_POLL_INTERVAL_MS);

refreshRestarts();
setInterval(refreshRestarts, SLOW_POLL_INTERVAL_MS);
