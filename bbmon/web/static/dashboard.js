"use strict";

// Polls the ping API and redraws the latency chart in place. Requirement 7
// asks for updates without a full page reload.

const POLL_INTERVAL_MS = 5000;

const chart = echarts.init(document.getElementById("latency-chart"), null, {
  renderer: "canvas",
});
const status = document.getElementById("status");

window.addEventListener("resize", () => chart.resize());

function baseOptions() {
  return {
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: "#8fa3b5" } },
    grid: { left: 48, right: 16, top: 36, bottom: 32 },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: "#24303c" } },
      axisLabel: { color: "#8fa3b5" },
    },
    yAxis: {
      type: "value",
      name: "ms",
      nameTextStyle: { color: "#8fa3b5" },
      axisLabel: { color: "#8fa3b5" },
      splitLine: { lineStyle: { color: "#24303c" } },
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
