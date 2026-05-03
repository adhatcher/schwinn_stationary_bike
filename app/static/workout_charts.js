(function () {
  const resizeCharts = () => {
    if (!window.Plotly || !window.Plotly.Plots) return;
    document.querySelectorAll('.js-plotly-chart .plotly-graph-div').forEach((chart) => {
      window.Plotly.Plots.resize(chart);
    });
  };

  window.addEventListener('load', () => window.setTimeout(resizeCharts, 0));
  window.addEventListener('resize', resizeCharts);
  window.addEventListener('orientationchange', () => window.setTimeout(resizeCharts, 250));
})();
