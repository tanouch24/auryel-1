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
    gtag('config', GA_ID);
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

  var consent = localStorage.getItem('auryel_cookie_ok');
  if (consent === '1') { loadGA(); loadPixel(); return; }
  if (consent === '0') return;

  document.addEventListener('DOMContentLoaded', function () {
    var banner = document.createElement('div');
    banner.id = 'cookie-banner';
    banner.style.cssText = [
      'position:fixed;bottom:0;left:0;right:0',
      'background:#0D0918',
      'border-top:1px solid rgba(200,169,110,0.18)',
      'padding:14px 24px',
      'z-index:99999',
      'box-shadow:0 -4px 32px rgba(0,0,0,0.5)'
    ].join(';');
    banner.innerHTML = [
      '<div style="max-width:900px;margin:0 auto;display:flex;align-items:center;',
      'gap:20px;flex-wrap:wrap;justify-content:space-between">',
      '<p style="margin:0;font-size:13px;font-family:Lora,Georgia,serif;',
      'color:#9C8E7A;line-height:1.55;flex:1;min-width:220px">',
      'Nous utilisons Google Analytics et le pixel Meta (Facebook/Instagram) pour mesurer ',
      'l\'audience du site et l\'efficacité de nos campagnes publicitaires. ',
      '<a href="/confidentialite" style="color:#C8A96E;text-decoration:none">',
      'En savoir plus</a>.',
      '</p>',
      '<div style="display:flex;gap:10px;flex-shrink:0">',
      '<button id="cb-accept" style="',
      'background:linear-gradient(135deg,#C8A96E,#E2C98A);',
      'color:#04020A;border:none;padding:9px 22px;',
      'font-family:Cinzel,serif;font-size:11px;letter-spacing:1.5px;',
      'cursor:pointer;font-weight:700">ACCEPTER</button>',
      '<button id="cb-refuse" style="',
      'background:transparent;color:#5E5247;',
      'border:1px solid rgba(200,169,110,0.2);padding:9px 22px;',
      'font-family:Cinzel,serif;font-size:11px;letter-spacing:1.5px;',
      'cursor:pointer">REFUSER</button>',
      '</div></div>'
    ].join('');
    document.body.appendChild(banner);

    document.getElementById('cb-accept').addEventListener('click', function () {
      localStorage.setItem('auryel_cookie_ok', '1');
      banner.remove();
      loadGA();
      loadPixel();
    });
    document.getElementById('cb-refuse').addEventListener('click', function () {
      localStorage.setItem('auryel_cookie_ok', '0');
      banner.remove();
    });
  });
})();
