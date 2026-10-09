/*
 * offer-banner.js — Offre exceptionnelle Auryel (09/10/2026).
 *
 * 1. Bandeau fin en haut de page : Premium 29,99 € barré -> 4,99 €/mois dans
 *    l'appli, lien Google Play (qui affiche « Installer » ou « Ouvrir » selon
 *    que l'appli est déjà là : le libellé reste donc neutre). Fermable
 *    (mémorisé 7 jours sur l'appareil).
 * 2. Fenêtre « avant de partir » : une fois par visite, quand la souris
 *    quitte la page par le haut (ordinateur) ou après 40 s et la moitié de la
 *    page lue (mobile). Jamais sur les pages légales ni de paiement.
 *
 * Aucun cookie, aucune donnée transmise : seulement localStorage /
 * sessionStorage pour ne pas réafficher.
 */
(function () {
  "use strict";

  var PLAY_URL = "https://play.google.com/store/apps/details?id=com.auryel.auryel";
  var SKIP = /\/(cgu|cgv|confidentialite|mentions-legales|cookies|suppression-compte|resiliation|payer|success|candidature-envoyee|404)(\.html)?$/;
  if (SKIP.test(location.pathname)) return;

  function store(kind) {
    try { return window[kind]; } catch (e) { return null; }
  }
  var local = store("localStorage");
  var session = store("sessionStorage");
  function get(s, k) { try { return s ? s.getItem(k) : null; } catch (e) { return null; } }
  function set(s, k, v) { try { if (s) s.setItem(k, v); } catch (e) {} }

  function track(name) {
    try { if (typeof window.gtag === "function") window.gtag("event", name, { offer: "premium_499" }); } catch (e) {}
  }

  var css =
    ".aof-bar{position:fixed;top:0;left:0;right:0;z-index:10000;box-sizing:border-box;display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;" +
    "padding:9px 44px 9px 16px;background:linear-gradient(90deg,#1B1238,#120E17);border-bottom:1px solid rgba(198,162,78,.35);" +
    "font:500 13px/1.35 Inter,system-ui,-apple-system,sans-serif;color:#F0E9DA;text-align:center}" +
    ".aof-badge{font-size:10px;font-weight:800;letter-spacing:.14em;color:#120E17;background:linear-gradient(135deg,#E4CE88,#C6A24E);padding:3px 8px;border-radius:20px}" +
    ".aof-strike{color:#9A8FA6;text-decoration:line-through}" +
    ".aof-price{color:#E4CE88;font-weight:700}" +
    ".aof-link{color:#E4CE88;font-weight:700;text-decoration:underline;text-underline-offset:3px}" +
    ".aof-x{position:absolute;right:10px;top:50%;transform:translateY(-50%);background:none;border:0;color:#9A8FA6;font-size:20px;line-height:1;cursor:pointer;padding:6px}" +
    ".aof-ov{position:fixed;inset:0;z-index:10001;background:rgba(8,6,12,.72);display:flex;align-items:center;justify-content:center;padding:16px;opacity:0;transition:opacity .25s ease}" +
    ".aof-ov.on{opacity:1}" +
    ".aof-box{position:relative;max-width:400px;width:100%;background:#171120;border:1px solid rgba(198,162,78,.4);border-radius:24px;padding:30px 24px 22px;text-align:center;color:#F0E9DA;font-family:Inter,system-ui,-apple-system,sans-serif;box-shadow:0 30px 80px rgba(0,0,0,.5)}" +
    ".aof-box h2{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:30px;line-height:1.1;color:#E4CE88;margin:14px 0 10px}" +
    ".aof-box p{font-size:14px;line-height:1.5;color:#ECE4D4;margin:0 0 6px}" +
    ".aof-big{font-size:22px!important;margin:12px 0 4px!important}" +
    ".aof-cta{display:block;margin-top:18px;padding:15px 20px;border-radius:30px;background:linear-gradient(180deg,#E4CE88,#C6A24E);color:#120E17;font-weight:700;font-size:15px;text-decoration:none}" +
    ".aof-no{display:inline-block;margin-top:10px;background:none;border:0;color:#9A8FA6;font-size:13px;cursor:pointer;padding:6px}" +
    "@media(max-width:560px){.aof-bar{flex-wrap:nowrap;gap:7px;padding:8px 34px 8px 10px;font-size:11.5px;white-space:nowrap}" +
    ".aof-bar .aof-badge{display:none}.aof-extra{display:none}.aof-x{right:4px}.aof-box h2{font-size:26px}}";

  function addStyle() {
    var s = document.createElement("style");
    s.textContent = css;
    document.head.appendChild(s);
  }

  // ── 1. Bandeau ──────────────────────────────────────────────────────────
  function showBar() {
    var hidden = Number(get(local, "aof_bar_hidden_until") || 0);
    if (hidden > Date.now()) return;
    var bar = document.createElement("div");
    bar.className = "aof-bar";
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "Offre exceptionnelle");
    bar.innerHTML =
      '<span class="aof-badge">OFFRE EXCEPTIONNELLE</span>' +
      '<span>Premium <span class="aof-strike">29,99 €</span> <span class="aof-price">4,99 €/mois</span><span class="aof-extra"> · 20 min offertes</span></span>' +
      '<a class="aof-link" href="' + PLAY_URL + '" target="_blank" rel="noopener noreferrer">Profiter de l’offre</a>' +
      '<button class="aof-x" type="button" aria-label="Fermer">×</button>';
    bar.querySelector(".aof-link").addEventListener("click", function () { track("offer_bar_click"); });
    document.body.insertBefore(bar, document.body.firstChild);

    // Décale le menu fixé en haut et le contenu de la hauteur du bandeau.
    var shifted = [];
    var basePad = parseFloat(getComputedStyle(document.body).paddingTop) || 0;
    function layout() {
      var h = bar.offsetHeight;
      document.body.style.paddingTop = basePad + h + "px";
      shifted.forEach(function (x) { x.el.style.top = x.top + h + "px"; });
    }
    Array.prototype.forEach.call(document.body.querySelectorAll("nav,header,[id=nav],.nav"), function (el) {
      var cs = getComputedStyle(el);
      if ((cs.position === "fixed" || cs.position === "sticky") && parseFloat(cs.top) < 5) {
        shifted.push({ el: el, top: parseFloat(cs.top) || 0 });
      }
    });
    layout();
    addEventListener("resize", layout, { passive: true });

    bar.querySelector(".aof-x").addEventListener("click", function () {
      set(local, "aof_bar_hidden_until", String(Date.now() + 7 * 864e5));
      removeEventListener("resize", layout);
      document.body.style.paddingTop = basePad ? basePad + "px" : "";
      shifted.forEach(function (x) { x.el.style.top = ""; });
      bar.remove();
    });
  }

  // ── 2. Fenêtre avant de partir ──────────────────────────────────────────
  var shown = false;
  function showExit() {
    if (shown || get(session, "aof_exit_shown")) return;
    if (document.querySelector(".aof-ov")) return;
    shown = true;
    set(session, "aof_exit_shown", "1");
    track("offer_exit_shown");
    var ov = document.createElement("div");
    ov.className = "aof-ov";
    ov.setAttribute("role", "dialog");
    ov.setAttribute("aria-modal", "true");
    ov.setAttribute("aria-label", "Offre exceptionnelle Auryel");
    ov.innerHTML =
      '<div class="aof-box">' +
      '<span class="aof-badge">OFFRE EXCEPTIONNELLE</span>' +
      "<h2>Avant de partir…</h2>" +
      "<p>Ton guide t’attend dans l’appli Auryel. Nouveau ? 20 minutes offertes pour commencer.</p>" +
      '<p class="aof-big"><span class="aof-strike">29,99 €</span> <span class="aof-price">4,99 €/mois</span></p>' +
      "<p>Premium : 4 h de consultation par mois, sans publicité, résiliable à tout moment.</p>" +
      '<a class="aof-cta" href="' + PLAY_URL + '" target="_blank" rel="noopener noreferrer">Profiter de l’offre</a>' +
      '<button class="aof-no" type="button">Non merci</button>' +
      "</div>";
    function close() {
      ov.classList.remove("on");
      setTimeout(function () { ov.remove(); }, 250);
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) { if (e.key === "Escape") close(); }
    ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
    ov.querySelector(".aof-no").addEventListener("click", close);
    ov.querySelector(".aof-cta").addEventListener("click", function () { track("offer_exit_click"); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(ov);
    requestAnimationFrame(function () { ov.classList.add("on"); });
    ov.querySelector(".aof-cta").focus();
  }

  function armExit() {
    var start = Date.now();
    var desktop = window.matchMedia && window.matchMedia("(pointer:fine)").matches;
    if (desktop) {
      document.addEventListener("mouseout", function (e) {
        if (!e.relatedTarget && e.clientY <= 0 && Date.now() - start > 8000) showExit();
      });
    } else {
      addEventListener("scroll", function () {
        var read = (scrollY + innerHeight) / Math.max(1, document.documentElement.scrollHeight);
        if (read > 0.5 && Date.now() - start > 40000) showExit();
      }, { passive: true });
    }
  }

  function init() {
    addStyle();
    showBar();
    armExit();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
