(function () {
  "use strict";

  window.TI_API_BASE_URL = window.TI_API_BASE_URL || (
    location.hostname === "localhost" || location.hostname === "127.0.0.1"
      ? "http://127.0.0.1:5000"
      : "https://traffic-intelligence-lji9.onrender.com"
  );

  window.tiApiUrl = function (path) {
    if (!path) return window.TI_API_BASE_URL;
    return path.startsWith("http://") || path.startsWith("https://")
      ? path
      : window.TI_API_BASE_URL + path;
  };
}());
