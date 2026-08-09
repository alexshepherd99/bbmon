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

async function refresh() {
  try {
    const response = await fetch("/api/ping");
    if (!response.ok) {
      throw new Error(`the server returned ${response.status}`);
    }
    const body = await response.json();
    const series = seriesFor(body.targets);
    const points = series.reduce((total, s) => total + s.data.length, 0);

    chart.setOption({ ...baseOptions(), series }, { replaceMerge: ["series"] });

    status.textContent = describe(points, body.generated_at);
    status.classList.remove("error");
  } catch (error) {
    status.textContent = `Could not load ping data: ${error.message}`;
    status.classList.add("error");
  }
}

refresh();
setInterval(refresh, POLL_INTERVAL_MS);
