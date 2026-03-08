/* ============================================================
   Coach — Athlete Dashboard Application
   Vanilla JS, Chart.js v4, dark theme
   ============================================================ */

(() => {
  'use strict';

  // -----------------------------------------------------------
  // Chart.js Global Configuration
  // -----------------------------------------------------------
  Chart.defaults.color = '#9898ab';
  Chart.defaults.borderColor = '#1e1e2e';
  Chart.defaults.font.family = "'Inter', sans-serif";

  // -----------------------------------------------------------
  // Color Constants
  // -----------------------------------------------------------
  const COLORS = {
    green:     '#22c55e',
    greenDim:  'rgba(34, 197, 94, 0.12)',
    yellow:    '#eab308',
    yellowDim: 'rgba(234, 179, 8, 0.12)',
    red:       '#ef4444',
    redDim:    'rgba(239, 68, 68, 0.12)',
    blue:      '#3b82f6',
    blueDim:   'rgba(59, 130, 246, 0.12)',
    purple:    '#8b5cf6',
    purpleDim: 'rgba(139, 92, 246, 0.12)',
    text:      '#f0f0f5',
    secondary: '#9898ab',
    tertiary:  '#5e5e72',
    grid:      '#1e1e2e',
  };

  // -----------------------------------------------------------
  // Chart Instance Registry (destroy before re-creating)
  // -----------------------------------------------------------
  const charts = {};

  // -----------------------------------------------------------
  // Auto-refresh timer
  // -----------------------------------------------------------
  let refreshTimer = null;

  // -----------------------------------------------------------
  // API Helpers
  // -----------------------------------------------------------
  async function fetchJSON(url) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (err) {
      console.error('[Coach] Failed to fetch ' + url + ':', err);
      return null;
    }
  }

  // -----------------------------------------------------------
  // Utility Helpers
  // -----------------------------------------------------------

  /** Format ISO date string to "Mar 7" style. */
  function fmtDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  /** Format minutes to "Xh Ym" or "Ym". */
  function fmtDuration(min) {
    if (min == null) return '--';
    const h = Math.floor(min / 60);
    const m = Math.round(min % 60);
    return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
  }

  /** Format km distance with one decimal, or null if missing. */
  function fmtDistance(km) {
    if (km == null || km === 0) return null;
    return km.toFixed(1) + ' km';
  }

  /** Current time as "HH:MM". */
  function currentTime() {
    const now = new Date();
    return String(now.getHours()).padStart(2, '0') + ':' +
           String(now.getMinutes()).padStart(2, '0');
  }

  /** Safe set textContent by element ID. Shows em-dash on null. */
  function setText(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = (value != null && value !== '') ? value : '\u2014';
  }

  /** Hide skeleton, reveal .card-content inside a card. */
  function revealCard(cardId) {
    const card = document.getElementById(cardId);
    if (!card) return;
    const skeleton = card.querySelector('.card-skeleton');
    const content = card.querySelector('.card-content');
    if (skeleton) skeleton.classList.add('hidden');
    if (content) content.classList.remove('hidden');
  }

  /** Return CSS class name for recovery score thresholds. */
  function recoveryColorClass(score) {
    if (score == null) return '';
    if (score > 70) return 'green';
    if (score >= 50) return 'yellow';
    return 'red';
  }

  /** Parse "H:MM:SS" to total seconds. */
  function timeToSeconds(t) {
    if (!t || typeof t !== 'string') return null;
    const parts = t.split(':').map(Number);
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return null;
  }

  /** Total seconds to "H:MM:SS". */
  function secondsToTime(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.round(s % 60);
    if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
    return m + ':' + String(sec).padStart(2, '0');
  }

  /** Escape HTML to prevent XSS. */
  function escapeHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str)));
    return div.innerHTML;
  }

  /** Normalize a sport/type string to a canonical name. */
  function normalizeSport(type) {
    if (!type) return 'Other';
    const t = type.toLowerCase();
    if (t.includes('run'))      return 'Run';
    if (t.includes('ride') || t.includes('cycl') || t.includes('bike')) return 'Ride';
    if (t.includes('strength') || t.includes('weight') || t.includes('gym')) return 'Strength';
    if (t.includes('swim'))     return 'Swim';
    return 'Other';
  }

  /** Get sport emoji and CSS class. */
  function sportInfo(type) {
    const sport = normalizeSport(type);
    const map = {
      Run:      { emoji: '\u{1F3C3}', cls: 'run' },
      Ride:     { emoji: '\u{1F6B4}', cls: 'ride' },
      Strength: { emoji: '\u{1F4AA}', cls: 'strength' },
      Swim:     { emoji: '\u{1F3CA}', cls: 'swim' },
      Other:    { emoji: '\u{1F3CB}\uFE0F', cls: 'other' },
    };
    return map[sport] || map.Other;
  }

  /** Sleep bar color by hours. */
  function sleepBarColor(hours) {
    if (hours >= 7) return COLORS.green;
    if (hours >= 6) return COLORS.yellow;
    return COLORS.red;
  }

  /** Compute a simple sleep trend from wellness array. */
  function computeSleepTrend(data) {
    if (!data || data.length < 4) return 'flat';
    const recent  = data.slice(-3).map(d => d.sleep_hours).filter(v => v != null);
    const earlier = data.slice(-6, -3).map(d => d.sleep_hours).filter(v => v != null);
    if (!recent.length || !earlier.length) return 'flat';
    const avgRecent  = recent.reduce((a, b) => a + b, 0) / recent.length;
    const avgEarlier = earlier.reduce((a, b) => a + b, 0) / earlier.length;
    const diff = avgRecent - avgEarlier;
    if (diff > 0.3) return 'up';
    if (diff < -0.3) return 'down';
    return 'flat';
  }

  // -----------------------------------------------------------
  // 1. Dashboard Snapshot
  // -----------------------------------------------------------
  async function loadDashboard() {
    const data = await fetchJSON('/api/dashboard');
    if (!data) return;

    const w = data.wellness || {};
    const rec = data.recovery || {};

    // -- Recovery Score card --
    const recoveryScore = rec.score;
    setText('recoveryScore', recoveryScore != null ? Math.round(recoveryScore) : null);
    const scoreEl = document.getElementById('recoveryScore');
    if (scoreEl) {
      scoreEl.className = 'metric-value';
      const cls = recoveryColorClass(recoveryScore);
      if (cls) scoreEl.classList.add('color-' + cls);
    }

    // Whoop recovery sub-line
    const whoopDiv = document.getElementById('whoopRecovery');
    if (data.whoop && data.whoop.recovery && data.whoop.recovery.recovery_score != null) {
      setText('whoopRecoveryVal', Math.round(data.whoop.recovery.recovery_score));
      if (whoopDiv) whoopDiv.style.display = '';
    } else {
      if (whoopDiv) whoopDiv.style.display = 'none';
    }
    revealCard('cardRecovery');

    // -- Sleep card --
    setText('sleepHours', w.sleep_hours != null ? Number(w.sleep_hours).toFixed(1) : null);
    setText('sleepQuality', w.sleep_score != null ? w.sleep_score + '%' : null);
    revealCard('cardSleep');

    // -- Training Load card --
    setText('ctlValue', w.ctl != null ? Math.round(w.ctl) : null);
    setText('atlValue', w.atl != null ? Math.round(w.atl) : null);

    const tsbVal = w.tsb;
    const tsbEl = document.getElementById('tsbValue');
    if (tsbEl) {
      tsbEl.textContent = tsbVal != null ? (tsbVal > 0 ? '+' : '') + Math.round(tsbVal) : '\u2014';
      tsbEl.className = 'trio-value tsb';
      if (tsbVal != null) {
        if (tsbVal > 5)       tsbEl.classList.add('positive');
        else if (tsbVal < -5) tsbEl.classList.add('negative');
        else                  tsbEl.classList.add('neutral');
      }
    }
    revealCard('cardLoad');

    // -- Strain card (Whoop) --
    const strainCard = document.getElementById('cardStrain');
    if (data.whoop && data.whoop.strain && data.whoop.strain.strain != null) {
      setText('strainValue', Number(data.whoop.strain.strain).toFixed(1));
      const kj = data.whoop.strain.kilojoule;
      setText('caloriesValue', kj != null ? Math.round(kj / 4.184).toLocaleString() : null);
      if (strainCard) strainCard.style.display = '';
      revealCard('cardStrain');
    } else {
      // Hide the entire strain card when no Whoop data
      if (strainCard) strainCard.style.display = 'none';
    }

    // -- Race countdown (header) --
    setText('daysToRace', data.days_to_race);
    setText('raceName', data.race_name || 'Race');

    // -- Recovery badge in header --
    const dot = document.getElementById('recoveryDot');
    const label = document.getElementById('recoveryLabel');
    if (dot) {
      dot.className = 'recovery-dot';
      const cls = recoveryColorClass(recoveryScore);
      if (cls) dot.classList.add(cls);
    }
    if (label) {
      if (rec.grade) {
        label.textContent = rec.grade;
      } else if (recoveryScore != null) {
        label.textContent = recoveryScore > 70 ? 'Good' : recoveryScore >= 50 ? 'Moderate' : 'Low';
      } else {
        label.textContent = '\u2014';
      }
    }

    // -- Last updated timestamp --
    setText('lastUpdated', currentTime());

    // -- Inline alerts / activities from dashboard --
    if (data.alerts) renderAlerts(data.alerts);
    if (data.today_activities) renderActivities(data.today_activities);
  }

  // -----------------------------------------------------------
  // 2. Sleep Data (chart + debt)
  // -----------------------------------------------------------
  async function loadSleep() {
    const data = await fetchJSON('/api/sleep?days=14');
    if (!data || !Array.isArray(data) || data.length === 0) return;

    // Populate sleep debt from most recent entry
    const latest = data[data.length - 1];
    if (latest && latest.debt != null) {
      setText('sleepDebt', Number(latest.debt).toFixed(1) + 'h');
    }

    buildSleepChart(data);
  }

  // -----------------------------------------------------------
  // 3a. Sleep Chart — bar chart, color-coded by hours
  // -----------------------------------------------------------
  function buildSleepChart(data) {
    if (!Array.isArray(data) || data.length === 0) return;
    const ctx = document.getElementById('sleepChart');
    if (!ctx) return;
    if (charts.sleep) charts.sleep.destroy();

    const labels = data.map(d => fmtDate(d.date));
    const hours  = data.map(d => d.whoop_hours || d.intervals_hours || d.sleep_hours || 0);
    const barColors = hours.map(sleepBarColor);

    charts.sleep = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'Sleep (hrs)',
          data: hours,
          backgroundColor: barColors.map(c => c + 'b3'),
          borderColor: barColors,
          borderWidth: 1,
          borderRadius: 4,
          borderSkipped: false,
          maxBarThickness: 32,
        }],
      },
      plugins: [{
        id: 'sleepTargetLine',
        afterDraw: function (chart) {
          const yAxis = chart.scales.y;
          const xAxis = chart.scales.x;
          if (!yAxis || !xAxis) return;
          const yPx = yAxis.getPixelForValue(7.5);
          const c = chart.ctx;
          c.save();
          c.beginPath();
          c.setLineDash([6, 4]);
          c.strokeStyle = COLORS.tertiary;
          c.lineWidth = 1.5;
          c.moveTo(xAxis.left, yPx);
          c.lineTo(xAxis.right, yPx);
          c.stroke();
          c.setLineDash([]);
          c.fillStyle = COLORS.tertiary;
          c.font = "10px 'Inter', sans-serif";
          c.textAlign = 'right';
          c.fillText('7.5h target', xAxis.right, yPx - 5);
          c.restore();
        },
      }],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) { return 'Sleep: ' + ctx.parsed.y.toFixed(1) + 'h'; },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxRotation: 0, maxTicksLimit: 7, font: { size: 10 } },
          },
          y: {
            beginAtZero: true,
            max: 10,
            grid: { color: COLORS.grid },
            ticks: {
              stepSize: 2,
              font: { size: 10 },
              callback: function (v) { return v + 'h'; },
            },
          },
        },
      },
    });
  }

  // -----------------------------------------------------------
  // Wellness Data — feeds fitness + HRV charts
  // -----------------------------------------------------------
  async function loadWellness() {
    const data = await fetchJSON('/api/wellness?days=14');
    if (!data || !Array.isArray(data) || data.length === 0) return;

    buildFitnessChart(data);
    buildHRVChart(data);

    // Set sleep trend arrow
    const trend = computeSleepTrend(data);
    const trendEl = document.getElementById('sleepTrend');
    if (trendEl) {
      trendEl.className = 'trend-arrow ' + trend;
      if (trend === 'up')        trendEl.textContent = '\u2191';
      else if (trend === 'down') trendEl.textContent = '\u2193';
      else                       trendEl.textContent = '\u2192';
    }
  }

  // -----------------------------------------------------------
  // 3b. Fitness Chart — CTL (blue), ATL (red dashed), TSB (green fill)
  // -----------------------------------------------------------
  function buildFitnessChart(data) {
    const ctx = document.getElementById('fitnessChart');
    if (!ctx) return;
    if (charts.fitness) charts.fitness.destroy();

    const labels  = data.map(d => fmtDate(d.date));
    const ctlData = data.map(d => d.ctl);
    const atlData = data.map(d => d.atl);
    const tsbData = data.map(d => d.tsb);

    charts.fitness = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Fitness (CTL)',
            data: ctlData,
            borderColor: COLORS.blue,
            backgroundColor: 'transparent',
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.3,
            yAxisID: 'y',
          },
          {
            label: 'Fatigue (ATL)',
            data: atlData,
            borderColor: COLORS.red,
            backgroundColor: 'transparent',
            borderWidth: 2,
            borderDash: [6, 3],
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.3,
            yAxisID: 'y',
          },
          {
            label: 'Form (TSB)',
            data: tsbData,
            borderColor: COLORS.green,
            backgroundColor: 'rgba(34, 197, 94, 0.08)',
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.3,
            fill: true,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: {
              boxWidth: 12, boxHeight: 2, padding: 16,
              font: { size: 10 },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxRotation: 0, maxTicksLimit: 7, font: { size: 10 } },
          },
          y: {
            type: 'linear',
            position: 'left',
            title: { display: true, text: 'CTL / ATL', font: { size: 10 }, color: COLORS.tertiary },
            grid: { color: COLORS.grid },
            ticks: { font: { size: 10 } },
          },
          y1: {
            type: 'linear',
            position: 'right',
            title: { display: true, text: 'TSB', font: { size: 10 }, color: COLORS.tertiary },
            grid: { drawOnChartArea: false },
            ticks: { font: { size: 10 } },
          },
        },
      },
    });
  }

  // -----------------------------------------------------------
  // 3c. HRV Chart — HRV (purple) left, RHR (red) right, baselines
  // -----------------------------------------------------------
  function buildHRVChart(data) {
    const ctx = document.getElementById('hrvChart');
    if (!ctx) return;
    if (charts.hrv) charts.hrv.destroy();

    const labels  = data.map(d => fmtDate(d.date));
    const hrvData = data.map(d => d.hrv);
    const rhrData = data.map(d => d.rhr);

    // Compute baseline averages
    const validHRV = hrvData.filter(v => v != null);
    const validRHR = rhrData.filter(v => v != null);
    const hrvBaseline = validHRV.length ? validHRV.reduce((a, b) => a + b, 0) / validHRV.length : null;
    const rhrBaseline = validRHR.length ? validRHR.reduce((a, b) => a + b, 0) / validRHR.length : null;

    charts.hrv = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'HRV (ms)',
            data: hrvData,
            borderColor: COLORS.purple,
            backgroundColor: COLORS.purpleDim,
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: COLORS.purple,
            pointHoverRadius: 5,
            tension: 0.3,
            fill: true,
            yAxisID: 'y',
          },
          {
            label: 'HRV baseline (' + (hrvBaseline != null ? Math.round(hrvBaseline) : '--') + ')',
            data: Array(labels.length).fill(hrvBaseline),
            borderColor: 'rgba(139, 92, 246, 0.35)',
            borderDash: [6, 4],
            borderWidth: 1,
            pointRadius: 0,
            fill: false,
            yAxisID: 'y',
          },
          {
            label: 'RHR (bpm)',
            data: rhrData,
            borderColor: COLORS.red,
            backgroundColor: 'transparent',
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: COLORS.red,
            pointHoverRadius: 5,
            tension: 0.3,
            yAxisID: 'y1',
          },
          {
            label: 'RHR baseline (' + (rhrBaseline != null ? Math.round(rhrBaseline) : '--') + ')',
            data: Array(labels.length).fill(rhrBaseline),
            borderColor: 'rgba(239, 68, 68, 0.35)',
            borderDash: [6, 4],
            borderWidth: 1,
            pointRadius: 0,
            fill: false,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: {
              boxWidth: 12, boxHeight: 2, padding: 16,
              font: { size: 10 },
              filter: function (item) {
                // Hide baseline entries from legend if desired
                return true;
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxRotation: 0, maxTicksLimit: 7, font: { size: 10 } },
          },
          y: {
            type: 'linear',
            position: 'left',
            title: { display: true, text: 'HRV (ms)', font: { size: 10, weight: '600' }, color: COLORS.purple },
            grid: { color: COLORS.grid },
            ticks: { font: { size: 10 }, color: COLORS.purple },
          },
          y1: {
            type: 'linear',
            position: 'right',
            title: { display: true, text: 'RHR (bpm)', font: { size: 10, weight: '600' }, color: COLORS.red },
            grid: { drawOnChartArea: false },
            ticks: { font: { size: 10 }, color: COLORS.red },
          },
        },
      },
    });
  }

  // -----------------------------------------------------------
  // Activities Data — list + load chart
  // -----------------------------------------------------------
  async function loadActivities() {
    const data = await fetchJSON('/api/activities?days=14');
    if (!data || !Array.isArray(data)) return;

    // Sort descending by date for the list display
    const sorted = [...data].sort((a, b) => (b.date || '').localeCompare(a.date || ''));
    renderActivities(sorted);
    buildLoadChart(data);
  }

  // -----------------------------------------------------------
  // 5. Activities Panel
  // -----------------------------------------------------------
  function renderActivities(activities) {
    const container = document.getElementById('activitiesList');
    if (!container) return;

    if (!activities || activities.length === 0) {
      container.innerHTML = '<div class="empty-state">No recent activities</div>';
      return;
    }

    container.innerHTML = activities.map(function (a) {
      const info = sportInfo(a.type);
      const dateStr = a.date ? fmtDate(a.date) : '';
      const dur  = fmtDuration(a.duration_min);
      const dist = fmtDistance(a.distance_km);

      var stats = '';
      if (a.tss != null) {
        stats += '<div class="activity-stat">' +
          '<div class="activity-stat-value">' + Math.round(a.tss) + '</div>' +
          '<div class="activity-stat-label">TSS</div></div>';
      }
      if (a.duration_min != null) {
        stats += '<div class="activity-stat">' +
          '<div class="activity-stat-value">' + dur + '</div>' +
          '<div class="activity-stat-label">Duration</div></div>';
      }
      if (dist) {
        stats += '<div class="activity-stat">' +
          '<div class="activity-stat-value">' + dist + '</div>' +
          '<div class="activity-stat-label">Distance</div></div>';
      }

      return '<div class="activity-item">' +
        '<div class="activity-type-icon ' + info.cls + '">' + info.emoji + '</div>' +
        '<div class="activity-info">' +
          '<div class="activity-name">' + escapeHtml(a.name || a.type || 'Activity') + '</div>' +
          '<div class="activity-date">' + dateStr + '</div>' +
        '</div>' +
        '<div class="activity-stats">' + stats + '</div>' +
      '</div>';
    }).join('');
  }

  // -----------------------------------------------------------
  // 3d. Load Chart — stacked bar of daily TSS by sport
  // -----------------------------------------------------------
  function buildLoadChart(data) {
    const ctx = document.getElementById('loadChart');
    if (!ctx) return;
    if (charts.load) charts.load.destroy();

    // Group TSS by date and normalized sport type
    var dateMap = {};
    var dateOrder = [];
    var seenDates = {};

    data.forEach(function (a) {
      if (!a.date) return;
      var label = fmtDate(a.date);
      if (!seenDates[label]) {
        seenDates[label] = true;
        dateOrder.push(label);
      }
      if (!dateMap[label]) dateMap[label] = {};
      var sport = normalizeSport(a.type);
      dateMap[label][sport] = (dateMap[label][sport] || 0) + (a.tss || 0);
    });

    var sportColorMap = {
      Run:      COLORS.green,
      Ride:     COLORS.blue,
      Strength: COLORS.purple,
      Swim:     COLORS.yellow,
      Other:    COLORS.secondary,
    };

    // Build one dataset per sport type that actually has data
    var allSports = ['Run', 'Ride', 'Strength', 'Swim', 'Other'];
    var usedSports = allSports.filter(function (sport) {
      return dateOrder.some(function (lbl) { return dateMap[lbl] && dateMap[lbl][sport] > 0; });
    });

    var datasets = usedSports.map(function (sport) {
      return {
        label: sport,
        data: dateOrder.map(function (lbl) { return (dateMap[lbl] && dateMap[lbl][sport]) || 0; }),
        backgroundColor: sportColorMap[sport] + 'cc',
        borderColor: sportColorMap[sport],
        borderWidth: 1,
        borderRadius: 3,
        borderSkipped: false,
        maxBarThickness: 32,
      };
    });

    charts.load = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: dateOrder,
        datasets: datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: {
              boxWidth: 12, boxHeight: 12, padding: 16,
              font: { size: 10 },
            },
          },
          tooltip: {
            callbacks: {
              label: function (ctx) { return ctx.dataset.label + ': ' + ctx.parsed.y + ' TSS'; },
            },
          },
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            ticks: { maxRotation: 0, maxTicksLimit: 7, font: { size: 10 } },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            grid: { color: COLORS.grid },
            title: { display: true, text: 'TSS', font: { size: 10 }, color: COLORS.tertiary },
            ticks: { font: { size: 10 } },
          },
        },
      },
    });
  }

  // -----------------------------------------------------------
  // 4. Alerts Panel
  // -----------------------------------------------------------
  async function loadAlerts() {
    const data = await fetchJSON('/api/alerts');
    if (!data) return;
    renderAlerts(data.alerts || []);
  }

  function renderAlerts(alerts) {
    const container = document.getElementById('alertsList');
    const countEl   = document.getElementById('alertCount');
    if (!container) return;

    if (!alerts || alerts.length === 0) {
      container.innerHTML = '<div class="empty-state">No alerts</div>';
      if (countEl) countEl.textContent = '0';
      return;
    }

    if (countEl) countEl.textContent = alerts.length;

    // Sort: critical first, then warning, then info
    var severityOrder = { critical: 0, warning: 1, info: 2 };
    var sorted = alerts.slice().sort(function (a, b) {
      return (severityOrder[a.severity] || 3) - (severityOrder[b.severity] || 3);
    });

    container.innerHTML = sorted.map(function (a) {
      var sev = a.severity || 'info';
      return '<div class="alert-item">' +
        '<span class="alert-severity ' + escapeHtml(sev) + '"></span>' +
        '<div class="alert-body">' +
          '<div class="alert-title">' + escapeHtml(a.title || 'Alert') + '</div>' +
          '<div class="alert-message">' + escapeHtml(a.message || '') + '</div>' +
        '</div>' +
        '<span class="alert-badge ' + escapeHtml(sev) + '">' + escapeHtml(sev) + '</span>' +
      '</div>';
    }).join('');
  }

  // -----------------------------------------------------------
  // 6. Race Prediction
  // -----------------------------------------------------------
  async function loadPrediction() {
    const data = await fetchJSON('/api/predictions');
    if (!data) return;

    const pred = data.prediction || {};

    // Race label
    var raceLabel = (data.race_name || '');
    if (data.race_date) raceLabel += ' ' + data.race_date;
    setText('predictionRace', raceLabel);

    // Times
    setText('predictedTime', pred.predicted_time || null);
    setText('goalTime', pred.target_time || pred.goal_time || null);

    // Gap calculation
    var gapEl = document.getElementById('predictionGap');
    if (gapEl) {
      var goalTimeStr = pred.target_time || pred.goal_time;
      if (pred.predicted_time && goalTimeStr) {
        var predSec = timeToSeconds(pred.predicted_time);
        var goalSec = timeToSeconds(goalTimeStr);
        if (predSec != null && goalSec != null) {
          var diff = predSec - goalSec;
          var sign = diff >= 0 ? '+' : '-';
          gapEl.textContent = sign + secondsToTime(Math.abs(diff));
          gapEl.style.color = diff <= 0 ? COLORS.green : COLORS.red;
        } else {
          gapEl.textContent = '\u2014';
        }
      } else {
        gapEl.textContent = '\u2014';
      }
    }

    // Confidence bar — can be a number (0-1 or 0-100) or a string ("low"/"medium"/"high")
    var conf = pred.confidence;
    var confBar = document.getElementById('confidenceBar');
    var confVal = document.getElementById('confidenceValue');
    var pct = 0;
    var confLabel = '\u2014';
    if (typeof conf === 'string') {
      var confMap = { low: 25, medium: 50, high: 75, very_high: 90 };
      pct = confMap[conf.toLowerCase()] || 30;
      confLabel = conf.charAt(0).toUpperCase() + conf.slice(1);
    } else if (typeof conf === 'number') {
      pct = conf <= 1 ? Math.round(conf * 100) : Math.round(conf);
      confLabel = pct + '%';
    }
    if (confBar) confBar.style.width = pct + '%';
    if (confVal) confVal.textContent = confLabel;

    // Methods
    var methodsEl = document.getElementById('predictionMethods');
    if (methodsEl && pred.methods) {
      // methods could be an array of {name, time} or an object {name: time}
      var methodsHTML = '';
      if (Array.isArray(pred.methods)) {
        methodsHTML = pred.methods.map(function (m) {
          return '<div class="method-item">' +
            '<span class="method-name">' + escapeHtml(m.name || m.method || '') + '</span>' +
            '<span class="method-value">' + escapeHtml(m.predicted_time || m.time || m.value || '') + '</span>' +
          '</div>';
        }).join('');
      } else if (typeof pred.methods === 'object') {
        methodsHTML = Object.keys(pred.methods).map(function (key) {
          return '<div class="method-item">' +
            '<span class="method-name">' + escapeHtml(key) + '</span>' +
            '<span class="method-value">' + escapeHtml(pred.methods[key]) + '</span>' +
          '</div>';
        }).join('');
      }
      methodsEl.innerHTML = methodsHTML;
    }

    // Limiting factors
    var factorsEl = document.getElementById('limitingFactors');
    if (factorsEl && pred.limiting_factors) {
      factorsEl.innerHTML = pred.limiting_factors.map(function (f) {
        var text = typeof f === 'string' ? f : (f.factor || f.name || '');
        return '<li class="factor-tag">' + escapeHtml(text) + '</li>';
      }).join('');
    }

    revealCard('cardPrediction');
  }

  // -----------------------------------------------------------
  // Refresh All Data
  // -----------------------------------------------------------
  async function refreshAll() {
    try {
      await Promise.all([
        loadDashboard(),
        loadSleep(),
        loadWellness(),
        loadActivities(),
        loadAlerts(),
        loadPrediction(),
        loadTrainingPlan(),
        loadWeather(),
        loadForecast(),
      ]);
    } catch (err) {
      console.error('[Coach] Refresh error:', err);
    }
  }

  // -----------------------------------------------------------
  // Training Plan
  // -----------------------------------------------------------

  async function loadTrainingPlan() {
    const data = await fetchJSON('/api/training-plan');
    if (!data) return;

    if (data.plan_exists && data.sessions && data.sessions.length > 0) {
      const phase = data.phase ? data.phase.charAt(0).toUpperCase() + data.phase.slice(1) : '';
      const wk = data.week_number ? 'Wk ' + data.week_number : '';
      const tss = data.target_tss ? ' \u00b7 ' + Math.round(data.target_tss) + ' TSS' : '';
      setText('trainingPhase', (phase + ' ' + wk + tss).trim() || '\u2014');
      renderTrainingSessions(data.sessions);
    } else {
      setText('trainingPhase', '\u2014');
      const container = document.getElementById('trainingSessionsList');
      if (container) container.innerHTML = '<div class="empty-state">No plan yet \u2014 send /plan in Telegram</div>';
    }
    revealCard('cardTrainingPlan');
  }

  function renderTrainingSessions(sessions) {
    const container = document.getElementById('trainingSessionsList');
    if (!container) return;
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div class="empty-state">No sessions this week</div>';
      return;
    }

    const today = new Date().toISOString().slice(0, 10);

    container.innerHTML = sessions.map(function (s) {
      const isToday = s.date === today;
      const isKey = s.is_key_session;
      const isRest = s.session_type === 'rest';

      const sportEmoji = s.sport === 'Run' ? '\u{1F3C3}' : s.sport === 'Ride' ? '\u{1F6B4}' : s.sport === 'Workout' ? '\u{1F3CB}\uFE0F' : '\u{1F4DD}';
      const durStr = s.duration_minutes > 0 ? fmtDuration(s.duration_minutes) : '';
      const zoneStr = s.intensity_zone && !isRest ? s.intensity_zone.toUpperCase() : '';
      const tssStr = s.target_tss > 0 ? Math.round(s.target_tss) + ' TSS' : '';
      const metaStr = [durStr, zoneStr, tssStr].filter(Boolean).join(' \u00b7 ');

      return '<div class="session-item' + (isToday ? ' session-today' : '') + '">' +
        '<div class="session-day">' + escapeHtml(s.day || '') + '</div>' +
        '<div class="session-emoji">' + sportEmoji + '</div>' +
        '<div class="session-info">' +
          '<div class="session-name">' + (isKey ? '<span class="session-key">\u2605</span>' : '') + escapeHtml(s.name) + '</div>' +
          (metaStr ? '<div class="session-meta">' + escapeHtml(metaStr) + '</div>' : '') +
        '</div>' +
      '</div>';
    }).join('');
  }

  // -----------------------------------------------------------
  // Weather
  // -----------------------------------------------------------

  async function loadWeather() {
    const data = await fetchJSON('/api/weather');
    if (!data) return;

    const desc = document.getElementById('weatherDesc');
    const content = document.getElementById('weatherContent');
    if (!content) return;

    if (!data.available) {
      if (desc) desc.textContent = 'Unavailable';
      content.innerHTML = '<div class="empty-state">' + escapeHtml(data.reason || 'Location not configured') + '</div>';
      revealCard('cardWeather');
      return;
    }

    if (desc) desc.textContent = escapeHtml(data.description || '\u2014');

    const tempStr = data.temperature_c != null ? data.temperature_c.toFixed(1) + '\u00b0C' : '\u2014';
    const feelsStr = data.feels_like_c != null ? data.feels_like_c.toFixed(1) + '\u00b0C' : '\u2014';
    const humStr = data.humidity_pct != null ? Math.round(data.humidity_pct) + '%' : '\u2014';
    const windStr = data.wind_speed_kmh != null ? Math.round(data.wind_speed_kmh) + ' km/h' : '\u2014';

    const paceAdj = data.pace_adjustment_pct || 0;
    const paceBadgeClass = paceAdj > 5 ? 'danger' : paceAdj > 0 ? 'warn' : 'ok';
    const paceBadgeText = paceAdj > 0 ? '+' + paceAdj.toFixed(1) + '% slower' : 'No adjustment';

    const hydStr = data.hydration_ml_per_hr ? data.hydration_ml_per_hr + ' ml/hr' : '\u2014';

    const warnings = (data.safety_warnings || []).map(function (w) {
      return '<div class="weather-warning">\u26a0\ufe0f ' + escapeHtml(w) + '</div>';
    }).join('');

    content.innerHTML =
      '<div class="weather-row"><span class="weather-label">Temperature</span><span class="weather-value">' + escapeHtml(tempStr) + ' <span style="color:var(--text-secondary);font-size:0.8rem">(feels ' + escapeHtml(feelsStr) + ')</span></span></div>' +
      '<div class="weather-row"><span class="weather-label">Humidity</span><span class="weather-value">' + escapeHtml(humStr) + '</span></div>' +
      '<div class="weather-row"><span class="weather-label">Wind</span><span class="weather-value">' + escapeHtml(windStr) + '</span></div>' +
      '<div class="weather-row"><span class="weather-label">Pace impact</span><span><span class="weather-badge ' + paceBadgeClass + '">' + escapeHtml(paceBadgeText) + '</span></span></div>' +
      '<div class="weather-row"><span class="weather-label">Hydration</span><span class="weather-value">' + escapeHtml(hydStr) + '</span></div>' +
      (data.clothing ? '<div class="weather-row"><span class="weather-label">Clothing</span><span class="weather-value" style="font-size:0.8rem;text-align:right;max-width:60%">' + escapeHtml(data.clothing) + '</span></div>' : '') +
      warnings;

    revealCard('cardWeather');
  }

  // -----------------------------------------------------------
  // Performance Forecast
  // -----------------------------------------------------------

  async function loadForecast() {
    const data = await fetchJSON('/api/performance-forecast');
    if (!data || data.error) return;

    buildForecastChart(data);

    const summaryEl = document.getElementById('forecastSummary');
    if (summaryEl) {
      const trendLabel = data.trend ? ' \u00b7 ' + data.trend : '';
      const confLabel = data.confidence ? ' \u00b7 ' + data.confidence + ' confidence' : '';
      var summary = 'CTL ' + data.current_ctl + ' \u2192 ' + data.predicted_ctl + trendLabel + confLabel;
      if (data.race_day) {
        summary += ' \u00b7 ' + data.race_day.race_name + ': CTL ' + data.race_day.predicted_ctl + ' in ' + data.race_day.days_to_race + 'd';
      }
      summaryEl.textContent = summary;
    }

    revealCard('cardForecast');
  }

  function buildForecastChart(data) {
    const ctx = document.getElementById('forecastChart');
    if (!ctx) return;
    if (charts.forecast) charts.forecast.destroy();

    const today = new Date();
    const horizonDays = data.horizon_days || 14;
    const labels = [];
    for (var i = 0; i <= horizonDays; i++) {
      const d = new Date(today);
      d.setDate(today.getDate() + i);
      labels.push(fmtDate(d.toISOString().slice(0, 10)));
    }

    const currentCTL = data.current_ctl || 0;
    const predictedCTL = parseFloat(data.predicted_ctl) || currentCTL;
    const ctlData = labels.map(function (_, idx) {
      return parseFloat((currentCTL + (predictedCTL - currentCTL) * (idx / horizonDays)).toFixed(1));
    });

    const currentTSB = data.current_tsb || 0;
    const predictedTSB = parseFloat(data.predicted_tsb) || currentTSB;
    const tsbData = labels.map(function (_, idx) {
      return parseFloat((currentTSB + (predictedTSB - currentTSB) * (idx / horizonDays)).toFixed(1));
    });

    const tsbColor = predictedTSB >= 0 ? COLORS.green : COLORS.red;

    charts.forecast = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'CTL',
            data: ctlData,
            borderColor: COLORS.blue,
            backgroundColor: 'rgba(59,130,246,0.08)',
            borderWidth: 2,
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 4,
          },
          {
            label: 'TSB',
            data: tsbData,
            borderColor: tsbColor,
            backgroundColor: 'transparent',
            borderWidth: 2,
            borderDash: [5, 3],
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 4,
            yAxisID: 'y2',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'top',
            align: 'end',
            labels: { boxWidth: 12, font: { size: 11 }, color: COLORS.secondary },
          },
          tooltip: {
            callbacks: {
              label: function (ctx) { return ' ' + ctx.dataset.label + ': ' + ctx.parsed.y; },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { font: { size: 10 }, color: COLORS.secondary, maxRotation: 0 },
          },
          y: {
            position: 'left',
            grid: { color: COLORS.grid },
            ticks: { font: { size: 10 }, color: COLORS.secondary },
            title: { display: true, text: 'CTL', color: COLORS.secondary, font: { size: 10 } },
          },
          y2: {
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { font: { size: 10 }, color: COLORS.secondary },
            title: { display: true, text: 'TSB', color: COLORS.secondary, font: { size: 10 } },
          },
        },
      },
    });
  }

  // -----------------------------------------------------------
  // Init — called on DOMContentLoaded
  // -----------------------------------------------------------
  async function init() {
    await refreshAll();

    // Auto-refresh every 5 minutes
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refreshAll, 5 * 60 * 1000);
  }

  // -----------------------------------------------------------
  // Boot
  // -----------------------------------------------------------
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
