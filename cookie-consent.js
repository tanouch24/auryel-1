(function () {
  var GA_ID = 'G-8R6DXZKJN6';
  var PIXEL_ID = '1757617121901396';

  function loadGA() {
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GA_ID;
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    function gtag() { dataLayer.push(arguments); }
    window.gtag = gtag;
    gtag('js', new Date());
    gtag('config', GA_ID, { cookie_expires: 34128000 }); // 13 mois (CNIL)
  }

  function loadPixel() {
    !function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){
    n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};
    if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
    n.queue=[];t=b.createElement(e);t.async=!0;
    t.src=v;s=b.getElementsByTagName(e)[0];
    s.parentNode.insertBefore(t,s)}(window,document,'script',
    'https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', PIXEL_ID);
    fbq('track', 'PageView');
  }

  // Retrait / modification du consentement (page Cookies) : on oublie le
  // choix et on recharge ; Google Analytics et le pixel ne sont plus chargés
  // tant que la personne n'a pas de nouveau accepté.
  window.auryelCookieReset = function () {
    localStorage.removeItem('auryel_cookie_ok');
    window.location.reload();
  };

  var consent = localStorage.getItem('auryel_cookie_ok');
  if (consent === '1') { loadGA(); loadPixel(); return; }
  if (consent === '0') return;

  document.addEventListener('DOMContentLoaded', function () {
    function clearBannerOffset() {
      document.documentElement.classList.remove('cookie-consent-visible');
      document.documentElement.style.removeProperty('--cookie-banner-offset');
      window.removeEventListener('resize', syncBannerOffset);
    }
    function syncBannerOffset() {
      document.documentElement.style.setProperty('--cookie-banner-offset', (banner.offsetHeight + 24) + 'px');
    }
    var banner = document.createElement('div');
    banner.id = 'cookie-banner';
    banner.style.cssText = [
      'position:fixed;bottom:12px;left:12px;right:12px',
      'max-width:760px;margin:0 auto',
      'background:rgba(28,22,38,0.97)',
      'border:1px solid rgba(198,162,78,0.28)',
      'border-radius:18px',
      'padding:16px 20px',
      'z-index:99999',
      'box-shadow:0 20px 60px rgba(0,0,0,0.55)'
    ].join(';');
    banner.innerHTML = [
      '<div style="max-width:900px;margin:0 auto;display:flex;align-items:center;',
      'gap:20px;flex-wrap:wrap;justify-content:space-between">',
      '<p style="margin:0;font-size:13px;font-family:Inter,system-ui,sans-serif;',
      'color:#CFC6D6;line-height:1.55;flex:1;min-width:220px">',
      'Nous utilisons Google Analytics et le pixel Meta (Facebook/Instagram) pour mesurer ',
      'l\'audience du site et l\'efficacité de nos campagnes publicitaires. ',
      '<a href="/confidentialite" style="color:#E4CE88;text-decoration:underline">',
      'En savoir plus</a>.',
      '</p>',
      '<div style="display:flex;gap:10px;flex-shrink:0">',
      '<button id="cb-accept" style="',
      'background:transparent;color:#F0E9DA;',
      'border:1px solid rgba(198,162,78,0.6);border-radius:999px;padding:10px 22px;',
      'font-family:Inter,system-ui,sans-serif;font-size:12px;letter-spacing:.08em;',
      'cursor:pointer;font-weight:600">Accepter</button>',
      '<button id="cb-refuse" style="',
      'background:transparent;color:#F0E9DA;',
      'border:1px solid rgba(198,162,78,0.6);border-radius:999px;padding:10px 22px;',
      'font-family:Inter,system-ui,sans-serif;font-size:12px;letter-spacing:.08em;',
      'cursor:pointer;font-weight:600">Refuser</button>',
      '</div></div>'
    ].join('');
    document.body.appendChild(banner);
    document.documentElement.classList.add('cookie-consent-visible');
    syncBannerOffset();
    window.addEventListener('resize', syncBannerOffset);

    document.getElementById('cb-accept').addEventListener('click', function () {
      localStorage.setItem('auryel_cookie_ok', '1');
      banner.remove();
      clearBannerOffset();
      loadGA();
      loadPixel();
    });
    document.getElementById('cb-refuse').addEventListener('click', function () {
      localStorage.setItem('auryel_cookie_ok', '0');
      banner.remove();
      clearBannerOffset();
    });
  });
})();
