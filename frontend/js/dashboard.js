/* ===========================================================================
   Traffic Intelligence — dashboard logic
   Hash-routed SPA views · SSE live telemetry · ECharts readouts ·
   server-paginated evidence/violations · uploads · reports · settings.
   =========================================================================== */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var API_BASE_URL = window.TI_API_BASE_URL || (
    (location.hostname === "localhost" || location.hostname === "127.0.0.1")
      ? "http://127.0.0.1:5000"
      : "https://traffic-intelligence-lji9.onrender.com"
  );

  function apiUrl(path) {
    if (!path) return API_BASE_URL;
    return /^https?:\/\//.test(path) ? path : API_BASE_URL + path;
  }

  var state = {
    sessionId: "live",
    jobId: null,
    lastUpdateAt: null,
    theme: localStorage.getItem("ti-theme") || "light",
    charts: {},
    vehiclePage: 1,
    vehiclePages: 1,
    violationPage: 1,
    violationPages: 1,
    isolatedSlice: -1,
    summaryCache: null,
    heatCache: null,
    classColors: { Car: "#24C0C0", Bus: "#0C489C", Truck: "#C07818",
                   Bike: "#48A848", Rickshaw: "#E46060" },
    evidenceCache: {},
    feedMode: "idle",   // idle | live | completed
    sse: null,
    sseRetryTimer: null,
    flashTimer: null,
    sourceFps: null,
  };

  /* ------------------------------------------------------------- helpers */
  function api(path, options) {
    var requestOptions = Object.assign({ credentials: "include" }, options || {});
    return fetch(apiUrl(path), requestOptions).then(function (resp) {
      if (resp.status === 401) {
        window.location.href = "/login.html";
        throw new Error("Sign in required.");
      }
      return resp.json().then(function (body) {
        if (!resp.ok || body.success === false) {
          throw new Error(body.error || ("Request failed (" + resp.status + ")"));
        }
        return body;
      });
    });
  }

  var ESC_MAP = { "&": "amp", "<": "lt", ">": "gt", '"': "quot" };
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"]/g, function (ch) {
      return "&" + ESC_MAP[ch] + ";";
    });
  }

  function fmtPct(value) {
    return value == null ? "—" : Math.round(value * 100) + "%";
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function viewActive(name) {
    var view = $("view-" + name);
    return !!(view && view.classList.contains("active"));
  }

  function toast(message, kind, actionLabel, actionFn) {
    var region = $("toastRegion");
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    var span = document.createElement("span");
    span.textContent = message;
    el.appendChild(span);
    if (actionLabel && typeof actionFn === "function") {
      var btn = document.createElement("button");
      btn.className = "toast-action";
      btn.type = "button";
      btn.textContent = actionLabel;
      btn.addEventListener("click", function () { actionFn(); el.remove(); });
      el.appendChild(btn);
    }
    region.appendChild(el);
    setTimeout(function () { el.remove(); }, 6000);
  }

  /* Count-up over 400ms whenever a metric value actually changes. */
  var counters = {};
  function setMetric(id, value, formatter) {
    var el = $(id);
    if (!el) return;
    var target = value;
    var format = formatter || function (v) { return String(v); };
    if (REDUCED_MOTION || typeof target !== "number" || isNaN(target)) {
      el.textContent = format(target);
      return;
    }
    var from = counters[id];
    if (from == null || from === target) {
      if (from === target) return;
      counters[id] = target;
      el.textContent = format(target);
      return;
    }
    counters[id] = target;
    var start = performance.now();
    var duration = 400;
    function step(now) {
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = format(from + (target - from) * eased);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* ------------------------------------------------------------ routing */
  var VIEWS = ["live", "camera", "overview", "heatmap", "violations", "evidence",
               "reports", "sessions", "settings"];

  function currentRoute() {
    var hash = location.hash.replace(/^#\/?/, "");
    return VIEWS.indexOf(hash) >= 0 ? hash : "live";
  }

  function navigate() {
    var route = currentRoute();
    VIEWS.forEach(function (name) {
      var view = $("view-" + name);
      if (view) view.classList.toggle("active", name === route);
    });
    document.querySelectorAll("[data-nav]").forEach(function (link) {
      link.classList.toggle("active", link.getAttribute("data-nav") === route);
    });
    // Charts need a visible container to measure themselves.
    if (route === "overview") { renderOverviewCharts(); }
    if (route === "heatmap") { renderHeatmap(); }
    if (route === "camera") { refreshCameraStatus(); }
    if (route === "violations") { loadViolations(); }
    if (route === "evidence") { loadVehicles(); }
    if (route === "sessions") { loadSessions(); }
    if (route === "settings") { loadSettings(); }
    if (route === "reports") { refreshReportLinks(); }
  }
  window.addEventListener("hashchange", navigate);

  /* --------------------------------------------------------------- theme */
  function applyTheme(theme) {
    state.theme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ti-theme", theme);
    var label = $("themeLabel");
    var iconName = theme === "dark" ? "sun" : "moon";
    if (label) label.textContent = theme === "dark" ? "Light" : "Dark";
    var toggle = $("themeToggle");
    if (toggle) {
      toggle.innerHTML =
        '<span data-lucide="' + iconName + '" style="width:14px;height:14px"></span><span id="themeLabel">' +
        (theme === "dark" ? "Light" : "Dark") + "</span>";
      if (window.lucide) lucide.createIcons();
    }
    Object.keys(state.charts).forEach(function (key) {
      var entry = state.charts[key];
      if (entry && entry.rerender) entry.rerender();
    });
  }

  /* --------------------------------------------------------------- clock */
  function tickClock() {
    var now = new Date();
    var utc = new Date(now.getTime() + now.getTimezoneOffset() * 60000);
    var h = String(now.getUTCHours()).padStart(2, "0");
    var m = String(now.getUTCMinutes()).padStart(2, "0");
    var s = String(now.getUTCSeconds()).padStart(2, "0");
    var el = $("hudClock");
    if (el) el.textContent = h + ":" + m + ":" + s;
    if (state.lastUpdateAt) {
      var ago = Math.max(0, Math.round((Date.now() - state.lastUpdateAt) / 1000));
      var label = $("systemStatus");
      if (label && !label.dataset.locked) {
        label.textContent = "System online · updated " + ago + "s ago";
      }
    }
  }
  setInterval(tickClock, 1000);

  /* -------------------------------------------------------------- health */
  function setHealth(id, value, cls) {
    var el = $(id);
    if (!el) return;
    el.textContent = value;
    el.className = cls || "";
  }

  function pollHealth() {
    api("/api/health").then(function (body) {
      var d = body.data;
      $("systemDot").className = "dot online";
      var statusEl = $("systemStatus");
      statusEl.dataset.locked = "";
      state.lastUpdateAt = state.lastUpdateAt || Date.now();
      setHealth("healthBackend", d.backend, "ok");
      var modelStatusRaw = d.ai_model || "";
      var modelLabel = (modelStatusRaw === "READY" || modelStatusRaw === "Loaded") ? "Ready" :
        (modelStatusRaw === "LOADING" ? "Loading" :
          (modelStatusRaw === "ERROR" || modelStatusRaw === "Failed" ? "Error" :
            (modelStatusRaw === "NOT_LOADED" ? "Not loaded" : (modelStatusRaw || "Not checked"))));
      var modelCls = (modelStatusRaw === "READY" || modelStatusRaw === "Loaded") ? "ok" :
        (modelStatusRaw === "ERROR" || modelStatusRaw === "Failed" ? "fail" : "idle");
      setHealth("healthModel", modelLabel, modelCls);
      setHealth("healthProcessor", d.video_processor,
        d.video_processor === "Processing" ? "ok" : "idle");
      setHealth("healthCamera", d.camera, d.camera === "Active" ? "ok" : "idle");
      setHealth("healthDatabase", d.database, d.database === "Connected" ? "ok" : "fail");
      $("healthError").classList.add("hidden");
      var failed = [];
      if (d.database === "Failed") failed.push("Database");
      if (d.ai_model === "Failed") failed.push("AI model");
      if (failed.length) showHealthError(failed.join(" and ") + " connection failed. Check Settings and retry.");
    }).catch(function () {
      $("systemDot").className = "dot offline";
      var statusEl = $("systemStatus");
      statusEl.dataset.locked = "1";
      statusEl.textContent = "Backend unreachable";
      showHealthError("Backend is unreachable. Confirm the Flask server is running, then retry.");
    });
  }

  function showHealthError(text) {
    $("healthErrorText").textContent = text;
    $("healthError").classList.remove("hidden");
  }

  /* ----------------------------------------------------- feed view modes */
  // live: MJPEG stream of real processed frames during PROCESSING.
  // completed: the SAVED processed video (same frames, encoded) in a <video>.
  function attachLiveStream() {
    var img = $("videoStream");
    var video = $("completedVideo");
    if (video) { video.pause(); video.classList.add("hidden"); video.removeAttribute("src"); }
    img.src = apiUrl("/video_feed?ts=" + Date.now());
    img.classList.remove("hidden");
    $("feedPlaceholder").classList.add("hidden");
    state.feedMode = "live";
    $("downloadProcessedFeedBtn").classList.add("hidden");
  }

  function enterCompletedMode(sessionId, autoplay) {
    var video = $("completedVideo");
    var img = $("videoStream");
    if (!video) return;
    img.removeAttribute("src");
    img.classList.add("hidden");
    video.src = apiUrl("/api/sessions/" + encodeURIComponent(sessionId) + "/processed-video");
    video.classList.remove("hidden");
    $("feedPlaceholder").classList.add("hidden");
    state.feedMode = "completed";
    state.sessionId = sessionId;
    var url = apiUrl("/api/sessions/" + encodeURIComponent(sessionId) + "/processed-video?download=1");
    var dl = $("downloadProcessedFeedBtn");
    dl.href = url;
    dl.classList.remove("hidden");
    if (autoplay) {
      var p = video.play();
      if (p && p.catch) p.catch(function () { /* autoplay blocked; controls remain */ });
    }
  }

  function enterIdleMode() {
    var video = $("completedVideo");
    var img = $("videoStream");
    if (video) { video.pause(); video.classList.add("hidden"); video.removeAttribute("src"); }
    img.removeAttribute("src");
    img.classList.add("hidden");
    $("downloadProcessedFeedBtn").classList.add("hidden");
    state.feedMode = "idle";
  }

  /* ----------------------------------------------------------- SSE feed */
  function flashBrackets() {
    if (REDUCED_MOTION) return;
    var corners = $("feedCorners");
    if (!corners) return;
    corners.classList.add("flash");
    clearTimeout(state.flashTimer);
    state.flashTimer = setTimeout(function () {
      corners.classList.remove("flash");
    }, 300);
  }

  function connectSSE() {
    if (state.sse) { state.sse.close(); state.sse = null; }
    if (!window.EventSource) return;
    var source = new EventSource(apiUrl("/api/sessions/" + state.sessionId + "/live"), { withCredentials: true });
    state.sse = source;

    source.addEventListener("open", function () { /* stream established */ });

    source.addEventListener("frame", function (evt) {
      var payload;
      try { payload = JSON.parse(evt.data); } catch (e) { return; }
      state.lastUpdateAt = Date.now();
      $("systemDot").className = "dot online";
      flashBrackets();

      // Overlays (boxes/labels/IDs/confidence/speed/violations) are baked
      // into the MJPEG frames by the server pipeline - no canvas re-draw.
      if (state.feedMode !== "live") attachLiveStream();
      $("hudState").textContent = "MONITORING";
      setHudStatus("PROCESSING");
      document.querySelector(".hud-rec .dot").style.background = "";
      $("hudFrame").textContent = payload.frame;
      $("hudFrameTotal").textContent = payload.total_frames ? " / " + payload.total_frames : "";
      $("hudActive").textContent = payload.vehicles;
      $("hudUnique").textContent = payload.unique_vehicles != null ? payload.unique_vehicles : "—";
      $("hudViol").textContent = payload.active_violations || 0;
      $("hudVehicles").textContent = payload.vehicles;
      $("hudConfidence").textContent = payload.confidence == null ? "—" : Math.round(payload.confidence * 100) + "%";
      $("hudFps").textContent = payload.fps == null ? "—" : Number(payload.fps).toFixed(1);

      $("insightCondition").textContent = payload.condition || "Unavailable";
      $("insightConditionNote").textContent = "Derived from frame " + payload.frame;
      $("insightVehicles").textContent = payload.vehicles;
      $("insightConfidence").textContent = payload.confidence == null ? "—" : fmtPct(payload.confidence);
      $("insightViolations").textContent = payload.active_violations || 0;
      $("insightCopy").textContent =
        "Reading live detector output. Speed values are estimates derived from configured pixel calibration.";

      setMetric("statVehicleCount", payload.vehicles);
      setMetric("statAvgSpeed", payload.avg_speed_kmh, function (v) { return Number(v).toFixed(1); });
      setMetric("statFps", payload.fps == null ? NaN : Number(Number(payload.fps).toFixed(1)),
        function (v) { return isNaN(v) ? "—" : String(v); });
      state.processing = true;
    });

    source.addEventListener("state", function (evt) {
      var payload;
      try { payload = JSON.parse(evt.data); } catch (e) { payload = {}; }
      if (payload.status === "done") {
        setFeedBadge("COMPLETED", "teal");
        $("hudState").textContent = "COMPLETE";
        setHudStatus("COMPLETED");
        $("stopProcessing").disabled = true;
        var pv = payload.processed_video;
        if (pv && !pv.partial) {
          toast("Processing complete — processed video saved.", "info", "Watch now", function () {
            enterCompletedMode(payload.job_id || state.jobId, true);
          });
          enterCompletedMode(payload.job_id || state.jobId, false);
        } else if (pv && pv.partial) {
          toast("Processing finished with a PARTIAL video (not verified).", "warn");
        } else {
          toast("Processing complete, but the processed video failed validation.", "error");
        }
        state.processing = false;
        state.jobId = null;
        refreshAll();
      } else if (payload.status === "cancelled") {
        setFeedBadge("CANCELLED · NOT VERIFIED", "amber");
        $("hudState").textContent = "STOPPED";
        setHudStatus("IDLE");
        $("stopProcessing").disabled = true;
        toast("Analysis cancelled — partial output is NOT VERIFIED.", "warn");
        state.processing = false;
        refreshAll();
      } else if (payload.status === "error") {
        setFeedBadge("FAILED", "red");
        $("hudState").textContent = "ERROR";
        setHudStatus("ERROR");
        $("stopProcessing").disabled = true;
        toast("Processing failed. Check the backend logs and retry.", "error");
        state.processing = false;
        state.jobId = null;
      } else if (payload.status === "idle" || payload.status === "empty") {
        setFeedBadge("IDLE", "");
        $("hudState").textContent = "IDLE";
        setHudStatus("IDLE");
      }
      source.close();
      state.sse = null;
      pollJobButtons();
    });

    source.onerror = function () {
      source.close();
      state.sse = null;
      if (state.processing || state.jobId) {
        clearTimeout(state.sseRetryTimer);
        state.sseRetryTimer = setTimeout(connectSSE, 1000);
      }
    };
  }

  function setFeedBadge(text, tone) {
    var badge = $("feedBadge");
    badge.textContent = text;
    badge.className = "badge" + (tone ? " " + tone : "");
  }

  /* ------------------------------------------------------------- upload */
  function uploadVideo(file) {
    if (!file) return;
    var form = new FormData();
    form.append("video", file);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("/api/upload"));
    xhr.withCredentials = true;
    xhr.upload.addEventListener("progress", function (evt) {
      if (evt.lengthComputable) {
        var pct = Math.round((evt.loaded / evt.total) * 100);
        setFeedBadge("UPLOADING " + pct + "%", "teal");
      }
    });
    xhr.addEventListener("load", function () {
      var body = {};
      try { body = JSON.parse(xhr.responseText); } catch (e) { /* noop */ }
      if (xhr.status >= 200 && xhr.status < 300 && body.job_id) {
        state.jobId = body.job_id;
        state.sessionId = "live";
        toast("Video uploaded. Processing frame 1…");
        setFeedBadge("PROCESSING", "teal");
        $("hudState").textContent = "MONITORING";
        setHudStatus("PROCESSING");
        $("stopProcessing").disabled = false;
        attachLiveStream();
        connectSSE();
        setTimeout(pollJobButtons, 500);
      } else {
        setFeedBadge("IDLE", "");
        toast(body.error || "Upload failed.", "error");
      }
    });
    xhr.addEventListener("error", function () {
      setFeedBadge("IDLE", "");
      toast("Upload failed — network error.", "error");
    });
    xhr.send(form);
  }

  function pollJobButtons() {
    api("/api/health").then(function (body) {
      var processing = !!body.data.processing;
      $("stopProcessing").disabled = !processing;
      if (!processing && $("feedBadge").textContent === "PROCESSING") {
        setFeedBadge("DONE", "teal");
      }
    }).catch(function () { /* handled by pollHealth */ });
  }

  /* ------------------------------------------------------- data loading */
  function refreshAll() {
    loadSummary();
    loadHeatmap();
    loadViolations();
    loadVehicles();
    loadSessions();
    refreshReportLinks();
  }

  function loadSummary() {
    api("/api/sessions/" + state.sessionId + "/summary").then(function (body) {
      var d = body.data;
      state.summaryCache = d;
      state.sourceFps = Number(d.fps) > 0 ? Number(d.fps) : null;
      state.lastUpdateAt = Date.now();
      var hasData = d.total_detections > 0;

      setMetric("statVehicleCount", d.current_vehicles);
      setMetric("statAvgSpeed", d.average_speed_kmh, function (v) {
        return v == null || isNaN(v) ? "—" : Number(v).toFixed(1);
      });
      setMetric("statDensity", d.traffic_density, function (v) { return Number(v).toFixed(4); });
      setMetric("statViolations", d.violation_events);
      setMetric("statTrafficFlow", d.traffic_flow === "N/A" ? NaN : NaN, function () { return d.traffic_flow; });
      $("statTrafficFlow").textContent = d.traffic_flow;
      if (d.processing_fps != null) {
        setMetric("statFps", Number(d.processing_fps), function (v) { return Number(v).toFixed(1); });
      } else {
        $("statFps").textContent = "—";
      }

      $("overviewScope").textContent = d.source_filename
        ? (d.source_filename + " · " + d.total_detections.toLocaleString() + " records")
        : "current session · latest processed frame";

      if (hasData) {
        $("insightCopy").textContent =
          "Detection data is available. Reports can be generated now.";
        $("reportStatus").textContent = "Detection data is available. Reports can be generated now.";
        $("reportBadge").textContent = d.verified ? "READY" : "NOT VERIFIED";
        $("reportBadge").className = "badge " + (d.verified ? "teal" : "amber");
        if (!$("insightCondition").dataset.live) {
          $("insightCondition").textContent = d.traffic_flow === "N/A" ? "Unavailable" : d.traffic_flow;
          $("insightConditionNote").textContent = "Derived from frame " + d.current_frame;
          $("insightVehicles").textContent = d.current_vehicles;
          $("insightConfidence").textContent = d.average_confidence == null ? "—" : fmtPct(d.average_confidence);
          $("insightViolations").textContent = d.violation_events;
        }
        var dominant = (d.distribution || []).slice().sort(function (a, b) { return b.count - a.count; })[0];
        $("insightDominant").textContent = dominant && dominant.count > 0
          ? dominant.type : "—";
        $("feedSessionNote").textContent = d.source_filename
          ? "Session " + String(d.session_id || "").slice(0, 8) + " · " + d.source_filename
          : "No feed connected yet";
        // Feed mode: while a job is streaming, the MJPEG live view stays.
        // For a finished session with a saved processed video, show the
        // SAVED video (same overlays) instead of re-attaching a stream.
        if (d.processed_video && d.status !== "processing" && state.feedMode !== "live") {
          enterCompletedMode(d.session_id || state.sessionId, false);
        } else if (!d.processed_video && state.feedMode === "idle" && d.status !== "processing") {
          $("feedPlaceholder").classList.remove("hidden");
        }
      } else {
        $("reportStatus").textContent =
          "No detection data yet. Upload a roadway video to enable reports.";
        $("reportBadge").textContent = "STANDBY";
        $("reportBadge").className = "badge";
      }
      delete $("insightCondition").dataset.live;

      // Lower HUD telemetry - real session values only. While a job is
      // actively streaming, ACTIVE/VIOLATIONS come from fresher SSE frames.
      $("hudUnique").textContent = d.unique_vehicles > 0 ? d.unique_vehicles : "—";
      $("hudAvg").textContent = d.average_speed_kmh > 0
        ? Number(d.average_speed_kmh).toFixed(1) : "—";
      if (!state.processing) {
        $("hudActive").textContent = d.current_vehicles;
        $("hudViol").textContent = d.violation_events;
      }

      // Status chip reflects real run state.
      if (d.status === "processing") setHudStatus("PROCESSING");
      else if (d.frames_processed > 0 && !state.processing) {
        setHudStatus(d.verified ? "COMPLETED" : "IDLE");
      }

      // Generate Report is enabled only for a completed, verified session.
      $("reportGenerate").disabled = !(hasData && !!d.verified && d.status !== "processing");

      renderDonut(d.distribution || []);
      renderTrace(d.trace || []);
    }).catch(function (err) {
      $("reportStatus").textContent = err.message;
    });
  }

  /* -------------------------------------------------------------- charts */
  function baseText() { return { color: cssVar("--text-secondary") }; }

  function registerChart(key, init) {
    var holder = $(key + "Chart") || $(key);
    if (!holder || !window.echarts) return null;
    var chart = echarts.init(holder);
    var entry = { chart: chart, rerender: function () { init(chart); } };
    state.charts[key] = entry;
    init(chart);
    window.addEventListener("resize", function () { chart.resize(); });
    return chart;
  }

  function renderDonut(distribution) {
    // While Overview is hidden we only refresh the cache; navigate()
    // re-renders from state.summaryCache when the view becomes visible.
    if (!viewActive("overview")) return;
    var total = distribution.reduce(function (sum, d) { return sum + d.count; }, 0);
    var empty = $("donutEmpty");
    if (total === 0) {
      empty.classList.remove("hidden");
      $("donutLegend").innerHTML = "";
      if (state.charts.donut) state.charts.donut.chart.clear();
      return;
    }
    empty.classList.add("hidden");

    var icons = { Car: "car", Bus: "bus", Truck: "truck", Bike: "bike", Rickshaw: "car-taxi-front" };

    function typeColor(type, fallbackIndex) {
      // ONE shared palette with the server-drawn bounding boxes.
      return state.classColors[type] ||
        ["#24C0C0", "#48A848", "#0C489C", "#C07818", "#E46060", "#97A1A3"][fallbackIndex % 6];
    }

    function draw(chart) {
      var seriesData = distribution.map(function (d, i) {
        return {
          name: d.type,
          value: d.count,
          itemStyle: {
            color: typeColor(d.type, i),
            opacity: state.isolatedSlice >= 0 && state.isolatedSlice !== i ? 0.22 : 1,
          },
        };
      });
      chart.setOption({
        textStyle: baseText(),
        tooltip: {
          trigger: "item",
          formatter: function (p) {
            return p.name + ": " + p.value + " (" + p.percent + "%)";
          },
        },
        series: [{
          type: "pie",
          radius: ["58%", "82%"],
          center: ["50%", "50%"],
          animationDuration: REDUCED_MOTION ? 0 : 900,
          animationEasing: "cubicOut",
          label: { show: false },
          data: seriesData,
        }],
      }, true);
    }

    if (!state.charts.donut) {
      registerChart("donut", draw);
    } else {
      draw(state.charts.donut.chart);
    }

    var legend = $("donutLegend");
    legend.innerHTML = distribution.map(function (d, i) {
      var swatch = typeColor(d.type, i);
      return '<button type="button" class="legend-row' +
        (state.isolatedSlice >= 0 && state.isolatedSlice !== i ? " dimmed" : "") +
        '" data-index="' + i + '">' +
        '<span class="swatch" style="background:' + swatch + '"></span>' +
        '<span class="l-icon"><i data-lucide="' + (icons[d.type] || "circle") + '" style="width:13px;height:13px"></i></span>' +
        "<span>" + esc(d.type) + "</span>" +
        '<span class="pct">' + d.percent.toFixed(1) + "% · " + d.count + "</span></button>";
    }).join("");
    if (window.lucide) lucide.createIcons();
    legend.querySelectorAll(".legend-row").forEach(function (row) {
      row.addEventListener("click", function () {
        var index = parseInt(row.getAttribute("data-index"), 10);
        state.isolatedSlice = state.isolatedSlice === index ? -1 : index;
        renderDonut(distribution);
      });
    });
  }

  function renderTrace(trace) {
    if (!viewActive("overview")) return;
    var empty = $("traceEmpty");
    if (!trace.length) {
      empty.classList.remove("hidden");
      if (state.charts.trace) state.charts.trace.chart.clear();
      return;
    }
    empty.classList.add("hidden");

    function draw(chart) {
      chart.setOption({
        textStyle: baseText(),
        animationDuration: REDUCED_MOTION ? 0 : 600,
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross", label: { backgroundColor: cssVar("--bg-chrome") } },
        },
        legend: { data: ["Vehicles", "Estimated speed (km/h)"], textStyle: baseText(), top: 0 },
        grid: { left: 44, right: 44, top: 34, bottom: 52 },
        xAxis: {
          type: "category",
          data: trace.map(function (t) { return t.time_s; }),
          name: "s",
          nameTextStyle: baseText(),
          axisLabel: Object.assign({ formatter: "{value}" }, baseText()),
          axisLine: { lineStyle: { color: cssVar("--border-subtle") } },
        },
        yAxis: [
          { type: "value", name: "Vehicles", axisLabel: baseText(),
            splitLine: { lineStyle: { color: cssVar("--border-subtle") } },
            nameTextStyle: baseText() },
          { type: "value", name: "km/h", axisLabel: baseText(),
            splitLine: { show: false }, nameTextStyle: baseText() },
        ],
        dataZoom: [
          { type: "inside", xAxisIndex: 0 },
          { type: "slider", xAxisIndex: 0, height: 18, bottom: 8 },
        ],
        series: [
          {
            name: "Vehicles",
            type: "line",
            smooth: true,
            showSymbol: false,
            data: trace.map(function (t) { return t.vehicles; }),
            lineStyle: { color: "#0C9C9C", width: 2 },
            itemStyle: { color: "#0C9C9C" },
            areaStyle: { color: "rgba(12,156,156,.08)" },
          },
          {
            name: "Estimated speed (km/h)",
            type: "line",
            smooth: true,
            showSymbol: false,
            yAxisIndex: 1,
            data: trace.map(function (t) { return t.avg_speed_kmh; }),
            lineStyle: { color: "#E46060", width: 2 },
            itemStyle: { color: "#E46060" },
          },
        ],
      }, true);
    }

    if (!state.charts.trace) registerChart("trace", draw);
    else draw(state.charts.trace.chart);
  }

  function fetchHeatmap() {
    // Always fetch (keeps the cache fresh during processing); render only
    // when the heatmap view is visible - ECharts needs a visible container.
    return api("/api/sessions/" + state.sessionId + "/heatmap").then(function (body) {
      state.heatCache = body.data;
      if (viewActive("heatmap")) drawHeatmap(body.data);
      return body.data;
    }).catch(function () { /* empty state already shown */ });
  }

  function renderHeatmap() {
    if (!viewActive("heatmap")) return;
    if (state.heatCache) { drawHeatmap(state.heatCache); return; }
    fetchHeatmap();
  }

  function drawHeatmap(heat) {
    var empty = $("heatmapEmpty");
    if (!heat || !heat.max) {
      empty.classList.remove("hidden");
      if (state.charts.heatmap) state.charts.heatmap.chart.clear();
      return;
    }
    empty.classList.add("hidden");

    // Video y grows downward; flip so the top of the frame sits on top.
    var yLabels = heat.y_labels.slice().reverse();
    var data = heat.data.map(function (point) {
      return [point[0], heat.bins_y - 1 - point[1], point[2]];
    });

    function draw(chart) {
      chart.setOption({
        textStyle: baseText(),
        tooltip: {
          position: "top",
          formatter: function (p) {
            return "x " + heat.x_labels[p.value[0]] + "px · y " +
              heat.y_labels[heat.bins_y - 1 - p.value[1]] + "px · " + p.value[2] + " detections";
          },
        },
        grid: { left: 56, right: 16, top: 12, bottom: 42 },
        xAxis: {
          type: "category",
          data: heat.x_labels,
          name: "X px",
          nameTextStyle: baseText(),
          axisLabel: { formatter: function (v) { return v; }, interval: 3 },
          splitArea: { show: false },
          axisLine: { show: false },
        },
        yAxis: {
          type: "category",
          data: yLabels,
          name: "Y px",
          nameTextStyle: baseText(),
          axisLabel: { interval: 2 },
          axisLine: { show: false },
        },
        visualMap: {
          min: 0,
          max: heat.max,
          calculable: false,
          orient: "horizontal",
          left: "center",
          bottom: 2,
          itemHeight: 90,
          inRange: { color: (heat.colormap || []).map(function (c) { return c[1]; }) },
          textStyle: baseText(),
        },
        series: [{
          type: "heatmap",
          data: data,
          progressive: 1000,
        }],
      }, true);
    }

    if (!state.charts.heatmap) registerChart("heatmap", draw);
    else draw(state.charts.heatmap.chart);
  }

  function setHudStatus(stateName) {
    // IDLE | PROCESSING | MONITORING | COMPLETED | ERROR
    var chip = $("hudStatusChip");
    if (!chip) return;
    var cls = "st-idle";
    if (stateName === "PROCESSING" || stateName === "MONITORING") cls = "st-processing";
    else if (stateName === "COMPLETED") cls = "st-completed";
    else if (stateName === "ERROR") cls = "st-error";
    chip.className = "hud-chip " + cls;
    chip.textContent = stateName;
  }

  function renderOverviewCharts() {
    var d = state.summaryCache;
    if (d) {
      renderDonut(d.distribution || []);
      renderTrace(d.trace || []);
    } else {
      loadSummary();
    }
  }

  function loadEvidenceCache() {
    var sid = state.sessionId;
    if (state.evidenceCache[sid]) {
      return Promise.resolve(state.evidenceCache[sid]);
    }
    return api("/api/sessions/" + sid + "/evidence").then(function (body) {
      var byVehicle = {};
      (body.data || []).forEach(function (item) {
        if (item.vehicle_id) byVehicle[item.vehicle_id] = item;
      });
      state.evidenceCache[sid] = byVehicle;
      return byVehicle;
    }).catch(function () { return {}; });
  }

  /* ---------------------------------------------------------- violations */
  function loadViolations() {
    var severity = $("violationSeverityFilter").value;
    var type = $("violationTypeFilter").value;
    var params = new URLSearchParams({
      page: state.violationPage, page_size: 20,
    });
    if (severity) params.set("severity", severity);
    if (type) params.set("type", type);
    Promise.all([
      api("/api/sessions/" + state.sessionId + "/violations?" + params),
      loadEvidenceCache(),
    ]).then(function (results) {
      var body = results[0];
      var evidence = results[1] || {};
      var rows = body.rows || [];
      state.violationPages = body.pages;
      var counts = body.counts || {};

      var flagBadge = $("violationFlagBadge");
      flagBadge.textContent = counts.all + " FLAGGED";
      flagBadge.className = "badge " + (counts.all > 0 ? "red" : "green");
      $("violationTotal").textContent = counts.all + " coalesced events";

      var list = $("violationList");
      if (!rows.length) {
        list.innerHTML = '<div class="violation-empty">' +
          '<b>No violations detected</b>' +
          '<span>The processor is monitoring supported speed violations.</span></div>';
      } else {
        list.innerHTML = rows.map(function (v) {
          var sevClass = v.severity === "severe" ? "speed-severe" : "speed-warning";
          var snap = evidence[v.vehicle_id];
          var snapLink = snap
            ? '<a class="text-button" href="' + esc(snap.url) + '" target="_blank" rel="noopener">Snapshot</a>'
            : "";
          return '<div class="violation-row">' +
            '<span class="badge red">DETECTED</span>' +
            '<span class="v-id">' + esc(v.vehicle_id) + "</span>" +
            "<span>" + esc(v.type) + "</span>" +
            '<span class="' + sevClass + '">▲ ' + v.peak_speed_kmh.toFixed(1) + " km/h est.</span>" +
            '<span class="v-window">' + v.start_time_s.toFixed(2) + "s – " + v.end_time_s.toFixed(2) + "s</span>" +
            '<span class="badge ' + (v.severity === "severe" ? "red" : "amber") + '">' +
            (v.severity === "severe" ? ">10% OVER" : "≤10% OVER") + "</span>" +
            snapLink +
            "</div>";
        }).join("");
      }
      $("violationCount").textContent = counts.all + " events shown";
      $("violationPage").textContent = body.page + " / " + body.pages;
      $("violationPrev").disabled = body.page <= 1;
      $("violationNext").disabled = body.page >= body.pages;

      // Populate the type filter once from unfiltered data.
      if (!type && !$("violationTypeFilter").dataset.filled) {
        api("/api/sessions/" + state.sessionId + "/violations?page_size=10").then(function (full) {
          var types = {};
          (full.rows || []).forEach(function (v) { types[v.type] = 1; });
          var select = $("violationTypeFilter");
          Object.keys(types).sort().forEach(function (t) {
            var opt = document.createElement("option");
            opt.value = t; opt.textContent = t;
            select.appendChild(opt);
          });
          select.dataset.filled = "1";
        }).catch(function () { /* non-critical */ });
      }
    }).catch(function (err) {
      $("violationList").innerHTML =
        '<div class="violation-empty"><b>Could not load violations</b><span>' +
        esc(err.message) + "</span></div>";
    });
  }

  /* ------------------------------------------------------------ evidence */
  var searchTimer = null;

  function openEvidenceModal(url, title, meta) {
    var modal = $("evidenceModal");
    var img = $("modalEvidenceImg");
    var titleEl = $("modalEvidenceTitle");
    var metaEl = $("modalEvidenceMeta");
    var openLink = $("modalEvidenceOpen");
    if (!modal || !img || !titleEl || !metaEl) return;
    img.src = url;
    img.alt = title;
    titleEl.textContent = title;
    metaEl.innerHTML = meta;
    if (openLink) {
      openLink.href = url;
      openLink.setAttribute("download", "evidence_snapshot.jpg");
    }
    modal.classList.remove("hidden");
    document.body.classList.add("modal-open");
  }

  function closeEvidenceModal() {
    var modal = $("evidenceModal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.classList.remove("modal-open");
  }

  function loadVehicles() {
    var q = $("vehicleSearch").value.trim();
    var type = $("vehicleTypeFilter").value;
    var violated = $("vehicleViolatedFilter").value;
    var params = new URLSearchParams({
      page: state.vehiclePage, page_size: 50,
    });
    if (q) params.set("q", q);
    if (type) params.set("type", type);
    if (violated) params.set("violated", violated);
    $("vehicleTableBody").innerHTML =
      '<tr><td colspan="8"><span class="skeleton" style="display:block;height:14px"></span></td></tr>';

    Promise.all([
      api("/api/sessions/" + state.sessionId + "/vehicles?" + params),
      loadEvidenceCache(),
    ]).then(function (results) {
      var body = results[0];
      var evidence = results[1] || {};
      var rows = body.rows || [];
      state.vehiclePages = body.pages;
      var tbody = $("vehicleTableBody");

      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="table-empty">No matching vehicle records.</td></tr>';
      } else {
        tbody.innerHTML = rows.map(function (v) {
          var conf = v.confidence;
          var band = conf == null ? "" : (conf < 0.4 ? "low" : conf <= 0.7 ? "mid" : "high");
          var width = conf == null ? 0 : Math.min(100, Math.round(conf * 100));
          var icons = { Car: "car", Bus: "bus", Truck: "truck", Bike: "bike", Rickshaw: "car-taxi-front" };
          var captureTime = state.sourceFps
            ? (v.last_seen_frame / state.sourceFps).toFixed(2) : "—";
          var evidenceItem = evidence[v.vehicle_id] || null;
          var evidenceBtn = evidenceItem
            ? '<button type="button" class="evidence-button" data-evidence-url="' + esc(evidenceItem.url) + '" data-evidence-title="' + esc("Vehicle " + v.vehicle_id) + '" data-evidence-meta="' + esc('<div><span>Type</span><strong>' + esc(v.type) + '</strong></div><div><span>Last speed</span><strong>' + Number(v.last_speed_kmh).toFixed(1) + ' km/h</strong></div><div><span>Capture</span><strong>' + esc(captureTime) + ' s</strong></div>') + '">Preview</button>'
            : '<span class="muted">No snapshot</span>';
          return "<tr>" +
            '<td class="mono">' + esc(v.vehicle_id) + "</td>" +
            '<td><span class="type-cell"><i data-lucide="' + (icons[v.type] || "circle") +
            '" style="width:14px;height:14px"></i>' + esc(v.type) + "</span></td>" +
            '<td style="color:var(--text-tertiary)">Unavailable</td>' +
            '<td class="mono">' + v.last_speed_kmh.toFixed(1) + " km/h</td>" +
            '<td class="mono">' + captureTime + " s</td>" +
            '<td><span class="conf-cell"><span class="conf-bar ' + band + '"><i style="width:' +
            width + '%"></i></span>' + (conf == null ? "—" : Math.round(conf * 100) + "%") + "</span></td>" +
            "<td>" + (v.ever_violated ? '<span class="badge red">Speeding</span>' : '<span style="color:var(--text-tertiary)">None</span>') + "</td>" +
            '<td><div class="row-stack"><span class="badge green">Detected</span>' + evidenceBtn + '</div></td>' +
            "</tr>";
        }).join("");
        if (window.lucide) lucide.createIcons();
      }
      $("vehicleCount").textContent = body.total.toLocaleString() + " vehicles shown";
      $("vehiclePage").textContent = body.page + " / " + body.pages;
      $("vehiclePrev").disabled = body.page <= 1;
      $("vehicleNext").disabled = body.page >= body.pages;

      var select = $("vehicleTypeFilter");
      var current = select.value;
      if (!type && body.types_present && body.types_present.length) {
        select.innerHTML = '<option value="">All types</option>' + body.types_present.map(function (t) {
          return '<option' + (t === current ? " selected" : "") + ">" + esc(t) + "</option>";
        }).join("");
      }
    }).catch(function (err) {
      $("vehicleTableBody").innerHTML =
        '<tr><td colspan="8" class="table-empty">' + esc(err.message) + "</td></tr>";
    });
  }

  /* ------------------------------------------------------------ sessions */
  function viewSession(sessionId, targetView) {
    // Point every analytics surface at the chosen session and reopen it.
    state.sessionId = sessionId;
    state.heatCache = null;
    state.evidenceCache = {};
    state.vehiclePage = 1;
    state.violationPage = 1;
    refreshAll();
    if (targetView) location.hash = "#/" + targetView;
    toast("Viewing session " + String(sessionId).slice(0, 8) + ".");
  }

  function loadSessions() {
    api("/api/sessions").then(function (body) {
      var sessions = body.data || [];
      var tbody = $("sessionTableBody");
      if (!sessions.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="table-empty">No sessions recorded yet.</td></tr>';
        return;
      }
      tbody.innerHTML = sessions.map(function (s) {
        var date = s.started_at ? new Date(s.started_at * 1000) : null;
        var dateStr = date ? date.toLocaleString() : "—";
        var statusClass = { completed: "session-status-completed",
                            cancelled: "session-status-cancelled",
                            processing: "session-status-processing" }[s.status] || "";
        var statusLabel = s.status === "cancelled"
          ? "Cancelled · NOT VERIFIED"
          : s.status.charAt(0).toUpperCase() + s.status.slice(1);
        var sid = encodeURIComponent(s.id);
        var pvCell = '<td class="mono">' +
          (s.has_processed
            ? (s.processed_partial
              ? "Partial · Not Verified"
              : ((s.processed_bytes || 0) / 1048576).toFixed(2) + " MB")
            : '<span style="color:var(--text-tertiary)">—</span>') + "</td>";
        var actions = '<td class="row-actions">';
        if (s.has_processed) {
          actions += '<button type="button" class="text-button" data-action="play" data-id="' + sid + '">Play</button>' +
            '<a class="text-button" href="' + apiUrl('/api/sessions/' + sid + '/processed-video?download=1') + '" download>Download</a>';
        }
        if (s.status !== "processing") {
          actions += '<button type="button" class="text-button" data-action="analysis" data-id="' + sid + '">Analysis</button>' +
            '<button type="button" class="text-button" data-action="evidence" data-id="' + sid + '">Evidence</button>';
        }
        actions += '<a class="text-button" href="/report.html?session=' + sid +
          '" target="_blank" rel="noopener">Report</a></td>';
        return "<tr>" +
          '<td class="mono">' + esc(dateStr) + "</td>" +
          "<td>" + esc(s.source_filename || "Unknown source") + "</td>" +
          '<td class="mono">' + (s.duration_s == null ? "—" : Number(s.duration_s).toFixed(1) + " s") + "</td>" +
          '<td class="mono">' + (s.vehicles_tracked || 0) + "</td>" +
          '<td class="mono">' + (s.detections || 0).toLocaleString() + "</td>" +
          '<td class="mono">' + (s.violations || 0) + "</td>" +
          '<td><span class="' + statusClass + '" style="font-weight:600;font-size:12px">' + statusLabel + "</span></td>" +
          pvCell + actions +
          "</tr>";
      }).join("");
      tbody.querySelectorAll("button[data-action]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var sid = decodeURIComponent(btn.getAttribute("data-id"));
          var action = btn.getAttribute("data-action");
          if (action === "play") {
            viewSession(sid, null);
            enterCompletedMode(sid, true);
            location.hash = "#/live";
          } else if (action === "analysis") {
            viewSession(sid, "overview");
          } else if (action === "evidence") {
            viewSession(sid, "evidence");
          }
        });
      });
    }).catch(function (err) {
      $("sessionTableBody").innerHTML =
        '<tr><td colspan="9" class="table-empty">' + esc(err.message) + "</td></tr>";
    });
  }

  /* ------------------------------------------------------------ settings */
  function loadSettings() {
    api("/api/config").then(function (body) {
      var c = body.data;
      $("cfgConfidence").value = c.confidence_threshold;
      $("cfgNms").value = c.nms_threshold;
      $("cfgAlert").value = c.alert_speed_threshold_kmh;
      $("cfgPpm").value = c.pixels_per_meter;
      $("cfgFps").value = c.video_fps;
      $("cfgTrajectoryLength").value = c.trajectory_length;
      $("cfgTrajectoryAlpha").value = c.trajectory_alpha;
      $("cfgTrajectoryThickness").value = c.trajectory_thickness;
      $("cfgTrajectorySmooth").value = c.trajectory_smooth_window;
      $("cfgTrajectoryMinAlpha").value = c.trajectory_min_alpha;
      $("cfgHeatlineGlow").value = c.heatline_glow;
      $("cfgHeatlineVisibility").value = c.heatline_visibility;
      var enabled = c.tracked_classes || [];
      document.querySelectorAll("#classChecks input").forEach(function (box) {
        box.checked = enabled.indexOf(box.value) >= 0;
      });
    }).catch(function (err) {
      $("settingsSaveNote").textContent = err.message;
    });
  }

  function saveSettings(evt) {
    evt.preventDefault();
    var classes = [];
    document.querySelectorAll("#classChecks input:checked").forEach(function (box) {
      classes.push(box.value);
    });
    var payload = {
      confidence_threshold: parseFloat($("cfgConfidence").value),
      nms_threshold: parseFloat($("cfgNms").value),
      alert_speed_threshold_kmh: parseFloat($("cfgAlert").value),
      pixels_per_meter: parseFloat($("cfgPpm").value),
      video_fps: parseFloat($("cfgFps").value),
      trajectory_length: parseInt($("cfgTrajectoryLength").value, 10),
      trajectory_alpha: parseFloat($("cfgTrajectoryAlpha").value),
      trajectory_thickness: parseInt($("cfgTrajectoryThickness").value, 10),
      trajectory_smooth_window: parseInt($("cfgTrajectorySmooth").value, 10),
      trajectory_min_alpha: parseFloat($("cfgTrajectoryMinAlpha").value),
      heatline_glow: parseFloat($("cfgHeatlineGlow").value),
      heatline_visibility: parseFloat($("cfgHeatlineVisibility").value),
      tracked_classes: classes,
    };
    api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (body) {
      $("settingsSaveNote").textContent = body.message || "Saved.";
      toast("Settings saved. Applies to the next analysis run.");
    }).catch(function (err) {
      $("settingsSaveNote").textContent = err.message;
      toast(err.message, "error");
    });
  }

  /* -------------------------------------------------------------- reports */
  function refreshReportLinks() {
    var base = apiUrl("/api/sessions/" + state.sessionId + "/report.");
    $("downloadCsvBtn").href = base + "csv";
    $("downloadPdfBtn").href = base + "pdf";
    $("downloadXlsxBtn").href = base + "xlsx";
    $("exportCsvBtn").onclick = function () {
      window.location.href = base + "csv";
    };
    // Processed-video download - only when the session actually has one.
    var pv = state.summaryCache && state.summaryCache.processed_video;
    var pvBtn = $("downloadProcessedBtn");
    if (pv && pv.download_url) {
      pvBtn.href = apiUrl(pv.download_url);
      pvBtn.classList.remove("hidden");
    } else {
      pvBtn.classList.add("hidden");
    }
  }

  function generateReport() {
    var btn = $("reportGenerate");
    if (btn.disabled) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Generating…';
    $("reportStatus").textContent = "Generating compiled report…";
    var url = apiUrl("/api/sessions/" + state.sessionId + "/report.pdf");
    fetch(url, { credentials: "include" }).then(function (resp) {
      if (!resp.ok) throw new Error("Report generation failed (" + resp.status + ")");
      return resp.blob();
    }).then(function (blob) {
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "traffic_report.pdf";
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast("Report generated.", "info", "View report", function () {
        window.open("/report.html?session=" + encodeURIComponent(state.sessionId), "_blank");
      });
      $("reportStatus").textContent = "Detection data is available. Reports can be generated now.";
    }).catch(function (err) {
      toast(err.message, "error");
      $("reportStatus").textContent = err.message;
    }).finally(function () {
      btn.disabled = false;
      btn.innerHTML = '<span data-lucide="file-output" style="width:14px;height:14px"></span> Generate report';
      if (window.lucide) lucide.createIcons();
    });
  }

  /* ---------------------------------------------------------------- wire */
  function wireEvents() {
    $("themeToggle").addEventListener("click", function () {
      applyTheme(state.theme === "dark" ? "light" : "dark");
    });

    var evidenceModal = $("evidenceModal");
    if (evidenceModal) {
      evidenceModal.addEventListener("click", function (event) {
        if (event.target === evidenceModal || event.target.dataset.closeModal === "true") {
          closeEvidenceModal();
        }
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !evidenceModal.classList.contains("hidden")) {
          closeEvidenceModal();
        }
      });
    }
    document.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-evidence-url]");
      if (!trigger) return;
      event.preventDefault();
      openEvidenceModal(trigger.getAttribute("data-evidence-url"),
        trigger.getAttribute("data-evidence-title") || "Vehicle evidence",
        trigger.getAttribute("data-evidence-meta") || "");
    });

    $("startProcessing").addEventListener("click", function () { $("fileInput").click(); });
    $("placeholderUploadBtn").addEventListener("click", function () { $("fileInput").click(); });
    $("fileInput").addEventListener("change", function () {
      if (this.files && this.files[0]) uploadVideo(this.files[0]);
      this.value = "";
    });

    $("stopProcessing").addEventListener("click", function () {
      var id = state.jobId;
      if (!id) {
        api("/api/health").then(function (body) {
          if (body.data.active_job_id) {
            state.jobId = body.data.active_job_id;
            return api("/api/jobs/" + state.jobId + "/cancel", { method: "POST" });
          }
          throw new Error("No processing job is active.");
        }).then(function () {
          toast("Analysis cancelled.", "warn");
        }).catch(function (err) { toast(err.message, "warn"); });
        return;
      }
      api("/api/jobs/" + id + "/cancel", { method: "POST" }).then(function () {
        toast("Analysis cancelled.", "warn");
        $("stopProcessing").disabled = true;
      }).catch(function (err) { toast(err.message, "error"); });
    });

    $("resetView").addEventListener("click", function () {
      state.isolatedSlice = -1;
      state.vehiclePage = 1;
      state.violationPage = 1;
      state.sessionId = "live";
      state.heatCache = null;
      state.evidenceCache = {};
      $("hudFrame").textContent = "—";
      $("hudFrameTotal").textContent = "";
      $("hudVehicles").textContent = "0";
      $("hudConfidence").textContent = "—";
      $("hudFps").textContent = "—";
      $("insightCondition").textContent = "Unavailable";
      $("insightConditionNote").textContent = "Derived from frame —";
      $("insightCopy").textContent = "Analysis will appear when the detector returns a processed frame.";
      enterIdleMode();
      $("feedPlaceholder").classList.remove("hidden");
      refreshAll();
      toast("View reset to the live session.");
    });

    $("fullscreenFeed").addEventListener("click", function () {
      var frame = document.querySelector(".feed-frame");
      if (document.fullscreenElement) document.exitFullscreen();
      else if (frame && frame.requestFullscreen) frame.requestFullscreen();
    });

    // Overlay toggle buttons (client-side presentation only).
    function toggleOverlayClass(name, btnId) {
      var btn = $(btnId);
      if (!btn) return;
      var frame = document.querySelector('.feed-frame');
      btn.addEventListener('click', function () {
        var active = btn.classList.toggle('active');
        try { localStorage.setItem(btnId, active ? '1' : '0'); } catch (e) { /* ignore */ }
        if (frame) {
          if (active) frame.classList.add('overlay-' + name);
          else frame.classList.remove('overlay-' + name);
        }
      });
      // Restore prior state
      try {
        var stored = localStorage.getItem(btnId);
        if (stored === '1') { btn.classList.add('active'); var frame = document.querySelector('.feed-frame'); if (frame) frame.classList.add('overlay-' + name); }
      } catch (e) { /* ignore */ }
    }

    toggleOverlayClass('trajectories', 'toggleTrajectories');
    toggleOverlayClass('heatmap', 'toggleHeatmap');
    toggleOverlayClass('boxes', 'toggleBoxes');
    toggleOverlayClass('labels', 'toggleLabels');

    // Live camera controls.
    $("cameraStart").addEventListener("click", function () {
      var raw = $("cameraSourceInput").value.trim() || "0";
      var source = /^\d+$/.test(raw) ? parseInt(raw, 10) : raw;
      this.disabled = true;
      api("/api/camera/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: source }),
      }).then(function () {
        toast("Camera started. Live detection running.");
        attachCameraStream(String(source));
      }).catch(function (err) {
        toast(err.message, "error");
      }).finally(function () {
        $("cameraStart").disabled = false;
        refreshCameraStatus();
      });
    });
    $("cameraStop").addEventListener("click", function () {
      api("/api/camera/stop", { method: "POST" }).then(function () {
        toast("Camera stopped.");
        $("cameraStream").classList.add("hidden");
        $("cameraStream").src = "";
        $("cameraPlaceholder").classList.remove("hidden");
        refreshCameraStatus();
      }).catch(function (err) { toast(err.message, "error"); });
    });

    $("healthRetry").addEventListener("click", pollHealth);

    // Account menu.
    var accountBtn = $("accountBtn");
    var accountMenu = $("accountMenu");
    accountBtn.addEventListener("click", function (evt) {
      evt.stopPropagation();
      accountMenu.classList.toggle("hidden");
      accountBtn.setAttribute("aria-expanded",
        String(!accountMenu.classList.contains("hidden")));
    });
    document.addEventListener("click", function () {
      accountMenu.classList.add("hidden");
      accountBtn.setAttribute("aria-expanded", "false");
    });
    $("logoutBtn").addEventListener("click", function () {
      api("/api/auth/logout", { method: "POST" }).then(function () {
        window.location.href = "/login.html";
      });
    });

    // Evidence: debounced search (250ms) + filters + pagination.
    $("vehicleSearch").addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        state.vehiclePage = 1;
        loadVehicles();
      }, 250);
    });
    $("vehicleTypeFilter").addEventListener("change", function () {
      state.vehiclePage = 1;
      loadVehicles();
    });
    $("vehicleViolatedFilter").addEventListener("change", function () {
      state.vehiclePage = 1;
      loadVehicles();
    });
    $("vehiclePrev").addEventListener("click", function () {
      if (state.vehiclePage > 1) { state.vehiclePage--; loadVehicles(); }
    });
    $("vehicleNext").addEventListener("click", function () {
      if (state.vehiclePage < state.vehiclePages) { state.vehiclePage++; loadVehicles(); }
    });

    // Violations filters + pagination.
    $("violationSeverityFilter").addEventListener("change", function () {
      state.violationPage = 1;
      loadViolations();
    });
    $("violationTypeFilter").addEventListener("change", function () {
      state.violationPage = 1;
      loadViolations();
    });
    $("violationPrev").addEventListener("click", function () {
      if (state.violationPage > 1) { state.violationPage--; loadViolations(); }
    });
    $("violationNext").addEventListener("click", function () {
      if (state.violationPage < state.violationPages) { state.violationPage++; loadViolations(); }
    });

    $("reportGenerate").addEventListener("click", generateReport);

    $("settingsForm").addEventListener("submit", saveSettings);

    // Roadway switcher (multi-camera-ready placeholder).
    var roadwayBtn = $("roadwayBtn");
    var roadwayMenu = $("roadwayMenu");
    roadwayBtn.addEventListener("click", function (evt) {
      evt.stopPropagation();
      var open = roadwayMenu.classList.toggle("hidden");
      roadwayBtn.setAttribute("aria-expanded", String(!open));
    });
    document.addEventListener("click", function () {
      roadwayMenu.classList.add("hidden");
      roadwayBtn.setAttribute("aria-expanded", "false");
    });
  }

  /* -------------------------------------------------------- live camera */
  function attachCameraStream(sourceLabel) {
    var img = $("cameraStream");
    img.src = apiUrl("/api/camera/stream?ts=" + Date.now());
    img.classList.remove("hidden");
    $("cameraPlaceholder").classList.add("hidden");
    $("cameraSourceLabel").textContent = sourceLabel;
  }

  // Attach the processed MJPEG feed to the live preview element.
  // This uses the authoritative /video_feed endpoint which serves
  // the processed frames with burned-in overlays when available.
  function attachLiveStream() {
    var img = $("videoStream");
    if (!img) return;
    // Attach MJPEG stream with cache-busting timestamp
    img.src = apiUrl("/video_feed?ts=" + Date.now());
    img.classList.remove("hidden");

    // Hide the completed <video> player (if visible)
    var completed = $("completedVideo");
    if (completed) {
      completed.classList.add("hidden");
      try { completed.pause(); completed.removeAttribute('src'); } catch (e) { /* ignore */ }
    }

    // Remove placeholder and ensure download button is hidden while live
    var ph = $("feedPlaceholder"); if (ph) ph.classList.add("hidden");
    var dl = $("downloadProcessedFeedBtn"); if (dl) dl.classList.add("hidden");

    // Ensure a lightweight overlay canvas exists for client-side presentation
    // (this does not replace server-drawn overlays; it's optional augmentation)
    var overlay = $("feedOverlay");
    var frame = document.querySelector('.feed-frame');
    if (frame && !overlay) {
      overlay = document.createElement('canvas');
      overlay.id = 'feedOverlay';
      overlay.className = 'feed-overlay';
      overlay.style.position = 'absolute';
      overlay.style.left = '0'; overlay.style.top = '0';
      overlay.style.width = '100%'; overlay.style.height = '100%';
      overlay.style.pointerEvents = 'none';
      frame.appendChild(overlay);
    } else if (overlay) {
      overlay.classList.remove('hidden');
    }

    state.feedMode = "live";
  }

  function refreshCameraStatus() {
    api("/api/camera/status").then(function (body) {
      var active = body.data.active;
      $("cameraBadge").textContent = active ? "LIVE" : "OFFLINE";
      $("cameraBadge").className = "badge " + (active ? "red" : "");
      $("cameraState").textContent = active ? "MONITORING" : "OFFLINE";
      document.querySelector("#view-camera .hud-rec .dot").style.background =
        active ? "" : "var(--text-tertiary)";
      $("cameraStop").disabled = !active;
      setHealth("healthCamera", active ? "Active" : "Inactive", active ? "ok" : "idle");
      if (active && !$("cameraStream").src) attachCameraStream("default");
    }).catch(function () { /* auth or network handled elsewhere */ });
  }

  setInterval(function () {
    var el = $("cameraClock");
    if (el) {
      var now = new Date();
      el.textContent = String(now.getUTCHours()).padStart(2, "0") + ":" +
        String(now.getUTCMinutes()).padStart(2, "0") + ":" +
        String(now.getUTCSeconds()).padStart(2, "0");
    }
  }, 1000);

  /* ------------------------------------------- live chart refresh loop */
  // While a job is actively processing, keep the donut, trace, heatmap,
  // evidence table, violations and metric cards updating in real time
  // instead of only on view switches or at job completion.
  setInterval(function () {
    api("/api/health").then(function (body) {
      var processing = !!body.data.processing;
      if (processing !== state.processing) {
        state.processing = processing;
        if (processing) toast("Processing started — live video, analytics and heatmap are updating.");
      }
      if (processing) {
        loadSummary();                       // guarded renders update cache + visible charts
        loadVehicles();
        fetchHeatmap();                      // live heatmap from real detection centers
        if (viewActive("violations")) loadViolations();
        if (viewActive("sessions")) loadSessions();
      }
    }).catch(function () { /* outages reported by pollHealth */ });
  }, 2000);

  /* ---------------------------------------------------------------- boot */
  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(state.theme);
    wireEvents();
    navigate();
    api("/api/auth/me").then(function (body) {
      var name = body.data.username;
      $("accountName").textContent = "Signed in as " + name;
      $("accountBtn").textContent = name.slice(0, 2).toUpperCase();
    }).catch(function () { /* redirect handled by api() */ });
    // Sync the ONE shared vehicle-type palette with the server pipeline.
    api("/api/config").then(function (body) {
      if (body.data && body.data.class_colors) state.classColors = body.data.class_colors;
      if (state.summaryCache) renderDonut(state.summaryCache.distribution || []);
    }).catch(function () { /* defaults already set */ });
    pollHealth();
    setInterval(pollHealth, 10000);
    loadSummary();
    connectSSE();       // picks up an already-running job, else goes idle
    refreshReportLinks();
    refreshCameraStatus();
    if (window.lucide) lucide.createIcons();
  });
})();
