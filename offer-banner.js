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
 * 3. Fenêtre de bienvenue « 20 minutes offertes » (une fois par jour, après
 *    un temps de lecture), sans doublon avec la fenêtre « avant de partir ».
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

  var shown = false;
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
    ".aof-hero{display:flex;justify-content:center;margin:-6px 0 6px}" +
    ".aof-phone{width:118px;border-radius:20px;padding:5px;background:linear-gradient(150deg,#2b2148,#100c16 70%);border:1px solid rgba(198,162,78,.35);box-shadow:0 18px 40px rgba(0,0,0,.5)}" +
    ".aof-phone img{display:block;width:100%;height:150px;object-fit:cover;object-position:top;border-radius:15px}" +
    ".aof-list{list-style:none;margin:14px 0 4px;padding:0;text-align:left;display:grid;gap:7px}" +
    ".aof-list li{position:relative;padding-left:20px;font-size:13.5px;line-height:1.45;color:#CFC6D6}" +
    ".aof-list li::before{content:'\\2726';position:absolute;left:0;top:1px;font-size:10px;color:#C6A24E}" +
    ".aof-legal{font-size:11px!important;color:#9A8FA6!important;margin-top:10px!important}" +
    "@media(max-width:820px){.aof-ov{inset:auto 0 0 0;background:none;padding:0 10px 10px;align-items:flex-end;pointer-events:none}" +
    ".aof-ov .aof-box{pointer-events:auto;max-width:520px;margin:0 auto;padding:16px 18px 12px;border-radius:22px;box-shadow:0 -10px 50px rgba(0,0,0,.55);transform:translateY(24px);transition:transform .3s ease}" +
    ".aof-ov.on .aof-box{transform:none}" +
    ".aof-ov .aof-hero,.aof-ov .aof-list,.aof-ov .aof-legal{display:none}" +
    ".aof-ov h2{font-size:23px!important;margin:8px 0 6px!important}.aof-ov p{font-size:13px}.aof-ov .aof-big{font-size:18px!important;margin:6px 0 2px!important}" +
    ".aof-ov .aof-cta{margin-top:12px;padding:13px 18px}.aof-ov .aof-no{margin-top:4px}}" +
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

    // Décale seulement le menu fixé en haut, par une translation : le
    // contenu ne bouge pas (pas de saut de mise en page, bon pour le SEO).
    // Le bandeau recouvre le haut de l'en-tête, qui est vide à cet endroit.
    var shifted = [];
    function layout() {
      var h = bar.offsetHeight;
      shifted.forEach(function (x) { x.el.style.transform = "translateY(" + h + "px)"; });
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
      shifted.forEach(function (x) { x.el.style.transform = ""; });
      bar.remove();
    });
  }

  // ── 2 bis. Fenêtre de bienvenue (09/10/2026) ────────────────────────────
  // Comme dans l'appli : télécharger Auryel et profiter des 20 minutes
  // offertes. Une fois par jour au plus, après un temps de lecture ; si elle
  // a été vue, la fenêtre « avant de partir » ne s'affiche pas en plus.
  function showWelcome() {
    if (Number(get(local, "aof_welcome_until") || 0) > Date.now()) return;
    if (get(session, "aof_exit_shown") || document.querySelector(".aof-ov")) return;
    set(local, "aof_welcome_until", String(Date.now() + 864e5));
    set(session, "aof_exit_shown", "1");
    shown = true;
    track("offer_welcome_shown");
    var ov = document.createElement("div");
    ov.className = "aof-ov";
    ov.setAttribute("role", "dialog");
    ov.setAttribute("aria-modal", "true");
    ov.setAttribute("aria-label", "20 minutes offertes dans l’application Auryel");
    ov.innerHTML =
      '<div class="aof-box">' +
      '<div class="aof-hero"><div class="aof-phone"><img src="/images/app/auryel-application-accueil.webp" alt="" width="540" height="1084"></div></div>' +
      '<span class="aof-badge">20 MINUTES OFFERTES</span>' +
      "<h2>Votre guide vous attend</h2>" +
      "<p>Téléchargez l’application Auryel : vos 20 premières minutes de consultation sont offertes.</p>" +
      '<ul class="aof-list"><li>Un guide attitré qui garde le fil de vos échanges</li><li>La pensée du jour et le réveil Auryel</li><li>L’Explorer : tarot, rêves, compatibilité…</li></ul>' +
      '<a class="aof-cta" href="' + PLAY_URL + '" target="_blank" rel="noopener noreferrer">Télécharger sur Google Play</a>' +
      '<button class="aof-no" type="button">Plus tard</button>' +
      '<p class="aof-legal">Une seule fois par compte, sans moyen de paiement. Les réponses des guides sont générées par une IA.</p>' +
      "</div>";
    function close() {
      ov.classList.remove("on");
      setTimeout(function () { ov.remove(); }, 250);
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) { if (e.key === "Escape") close(); }
    ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
    ov.querySelector(".aof-no").addEventListener("click", close);
    ov.querySelector(".aof-cta").addEventListener("click", function () { track("offer_welcome_click"); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(ov);
    requestAnimationFrame(function () { ov.classList.add("on"); });
    if (!(window.matchMedia && window.matchMedia("(max-width:820px)").matches)) ov.querySelector(".aof-cta").focus();
    else ov.setAttribute("aria-modal", "false");
  }

  function armWelcome() {
    var start = Date.now(), done = false;
    function tryShow() {
      if (done) return;
      var read = (scrollY + innerHeight) / Math.max(1, document.documentElement.scrollHeight);
      if (Date.now() - start > 9000 && (read > 0.25 || Date.now() - start > 20000)) { done = true; showWelcome(); }
    }
    addEventListener("scroll", tryShow, { passive: true });
    setTimeout(tryShow, 20500);
  }

  // ── 2. Fenêtre avant de partir ──────────────────────────────────────────
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
    if (!(window.matchMedia && window.matchMedia("(max-width:820px)").matches)) ov.querySelector(".aof-cta").focus();
    else ov.setAttribute("aria-modal", "false");
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
    armWelcome();
    armExit();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
