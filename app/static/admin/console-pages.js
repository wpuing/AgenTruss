/**
 * Compatibility shim — domain pages moved to pages-ext-*.js (AgenTrussPageModules).
 * Kept so older notes / caches referencing console-pages.js still load safely.
 */
(function () {
  "use strict";
  window.AgenTrussRegisterPages = function register(_H) {
    return {};
  };
})();
