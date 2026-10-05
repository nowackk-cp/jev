/* Jev ofisi sahnesi: duvar, cam planlama odası, beyaz tahta, pencere, çay ocağı ve masalar.
 * Üç katman üretir: back() karakterlerin arkası, glass() cam odanın ön camı, front() masaların önü.
 * Koordinatlar 1600×900 sahne birimidir; office.js karakterleri, levhaları ve balonları aşağıdaki noktalara koyar.
 * Sahne sayfada bir kez kurulduğu için burada kimlik (id) kullanılabilir.
 */
(function () {
  "use strict";

  const W = 1600, H = 900;
  const DESKS = [
    { key: "sol", cx: 300 },
    { key: "sonnet", cx: 660 },
    { key: "luna", cx: 1020 },
    { key: "uzman", cx: 1380 },
  ];
  const LAMPS = [300, 660, 1020, 1380];

  // Ayak noktaları (x, y), katman sırası (z), bakış yönü ve balonun yatay kayması (dx)
  const SPOTS = {
    room: { x: 230, y: 558, z: 10 },
    tea: { x: 1330, y: 578, z: 30 },
    home: { x: 1235, y: 585, z: 30 },
    consult: { x: 490, y: 585, z: 30, dir: "l", dx: 30 },
  };
  // Camlı planlama odası: içindeki noktalar camın arkasında kalır (z 10); odaya kapıdan girilip çıkılır
  const ROOM = { right: 430, front: 575, door: 375 };
  // İsim levhaları (sahne birimiyle dikdörtgen); compact levhada unvan satırı yoktur, sign levhası camın üstündeki tabeladır
  const PLATES = {
    opus: { x: 50, y: 75, w: 200, h: 55, sign: true, compact: true },
    jev: { x: 1425, y: 398, w: 145, h: 64 },
  };
  for (const d of DESKS) {
    SPOTS["desk-" + d.key] = { x: d.cx + 35, y: 738, z: 40 };
    SPOTS["front-" + d.key] = { x: d.cx - 110, y: 895, z: 60 };
    PLATES["desk-" + d.key] = { x: d.cx - 15, y: 708, w: 155, h: 77 };
  }
  // Yürüme yolları: arka koridor (duvarla masalar arası), ön koridor ve masalar arasındaki geçitler
  const AISLE = { back: 600, front: 895, gaps: [480, 840, 1200] };

  const r1 = (v) => Math.round(v * 10) / 10;
  const svg = (cls, body) =>
    `<svg class="scene ${cls}" viewBox="0 0 ${W} ${H}" aria-hidden="true" focusable="false">${body}</svg>`;

  function starPath(x, y, ro, ri) {
    let d = "";
    for (let i = 0; i < 10; i++) {
      const a = (Math.PI / 5) * i - Math.PI / 2, r = i % 2 ? ri : ro;
      d += (i ? "L" : "M") + r1(x + r * Math.cos(a)) + " " + r1(y + r * Math.sin(a)) + " ";
    }
    return d + "Z";
  }

  function defs() {
    return `<defs>
      <pattern id="p-wall" width="48" height="48" patternUnits="userSpaceOnUse">
        <rect class="s-wall" width="48" height="48"/>
        <circle class="s-wall2" cx="12" cy="12" r="2.2"/><circle class="s-wall2" cx="36" cy="36" r="2.2"/>
      </pattern>
      <pattern id="p-planks" x="0" y="470" width="200" height="60" patternUnits="userSpaceOnUse">
        <rect class="s-floor1" width="200" height="30"/><rect class="s-floor2" y="30" width="200" height="30"/>
        <path class="s-floorline" d="M0 1 H200 M0 30 H200 M70 1 V30 M170 30 V60"/>
      </pattern>
      <pattern id="p-tiles" x="30" y="440" width="50" height="27" patternUnits="userSpaceOnUse">
        <rect class="s-roomfloor" width="50" height="27"/>
        <path class="s-roomline" d="M0 .75 H50 M.75 0 V27"/>
      </pattern>
      <linearGradient id="g-floorshade" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#000" stop-opacity=".16"/><stop offset=".28" stop-color="#000" stop-opacity="0"/>
      </linearGradient>
      <linearGradient id="g-sky" gradientUnits="userSpaceOnUse" x1="0" y1="80" x2="0" y2="280">
        <stop class="sky1" offset="0"/><stop class="sky2" offset="1"/>
      </linearGradient>
      <linearGradient id="g-cone" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#ffd98a" stop-opacity=".42"/><stop offset="1" stop-color="#ffd98a" stop-opacity="0"/>
      </linearGradient>
      <radialGradient id="g-screen">
        <stop offset="0" stop-color="#a8dcff" stop-opacity=".8"/><stop offset=".6" stop-color="#a8dcff" stop-opacity=".25"/>
        <stop offset="1" stop-color="#a8dcff" stop-opacity="0"/>
      </radialGradient>
      <clipPath id="c-win"><rect x="990" y="80" width="200" height="200"/></clipPath>
    </defs>`;
  }

  function wall(x0 = 0, w = W) {
    let panels = "";
    for (let x = x0; x < x0 + w; x += 80) panels += `<rect class="s-panel" x="${x + 10}" y="391" width="60" height="56" rx="4"/>`;
    return `
      <rect x="${x0}" y="0" width="${w}" height="470" fill="url(#p-wall)"/>
      <rect class="s-base" x="${x0}" y="0" width="${w}" height="9"/>
      <rect class="s-wall2" x="${x0}" y="380" width="${w}" height="80"/>
      ${panels}
      <rect class="s-base" x="${x0}" y="374" width="${w}" height="7" rx="2"/>
      <rect class="s-base" x="${x0}" y="458" width="${w}" height="14"/>
      <rect x="${x0}" y="470" width="${w}" height="430" fill="url(#p-planks)"/>
      <rect x="${x0}" y="470" width="${w}" height="430" fill="url(#g-floorshade)"/>`;
  }

  // Geniş ekranda sahnenin iki yanı boş kalmasın: duvar ve zemin kenarlara uzar. "slice" ile ölçek yüksekliğe
  // bağlıdır ve orta nokta sahnenin ortasıdır, desenler de (back()'teki tanımlar) aynı hizada devam eder.
  function backdrop() {
    return `<svg class="stage-backdrop" viewBox="${W / 2 - 3200} 0 6400 ${H}" preserveAspectRatio="xMidYMid slice"
      aria-hidden="true" focusable="false">${wall(W / 2 - 3200, 6400)}</svg>`;
  }

  // Gece lambaların ışığı duvara ve zemine düşer
  function cones() {
    return LAMPS.map((x) =>
      `<path class="s-lampglow" d="M${x - 20} 54 L${x - 150} 684 L${x + 150} 684 L${x + 20} 54 Z" fill="url(#g-cone)"/>`).join("");
  }

  function lamps() {
    return LAMPS.map((x) => `
      <line class="s-cord" x1="${x}" y1="9" x2="${x}" y2="32"/>
      <path class="s-lamp" d="M${x - 25} 52 Q${x - 23} 31 ${x} 30 Q${x + 23} 31 ${x + 25} 52 Z"/>
      <rect class="s-lamp2" x="${x - 26}" y="49" width="52" height="5" rx="2.5"/>
      <ellipse class="s-bulb" cx="${x}" cy="56" rx="9" ry="4"/>`).join("");
  }

  // Planlama odası: iç duvar, fayans, iğnelenmiş çizim, rafta rulo planlar, çizim masası, saksı
  function room() {
    return `<g>
      <rect class="s-roomwall" x="30" y="60" width="400" height="380"/>
      <rect x="30" y="440" width="400" height="135" fill="url(#p-tiles)"/>
      <rect class="s-shadow2" x="30" y="440" width="400" height="7"/>
      <g transform="rotate(-2 225 190)">
        <rect x="170" y="150" width="110" height="80" rx="2" fill="#2f6fb3"/>
        <path d="M170 170 H280 M170 190 H280 M170 210 H280 M192 150 V230 M214 150 V230 M236 150 V230 M258 150 V230" stroke="#fff" stroke-opacity=".13"/>
        <path d="M186 218 V190 L211 170 L236 190 V218 Z M203 218 V201 H219 V218 M246 218 V182 H266 V218 M243 183 L256 172 L269 183" stroke="#e8f1ff" stroke-width="2" fill="none" stroke-linejoin="round"/>
        <circle cx="176" cy="156" r="3.5" fill="#d64545"/><circle cx="274" cy="156" r="3.5" fill="#d64545"/>
      </g>
      <g transform="rotate(3 335 186)">
        <rect class="s-paper" x="300" y="160" width="70" height="52" rx="2"/>
        <path d="M308 173 h40 M308 182 h54 M308 191 h30 M308 200 h46" stroke="#9aa4ae" stroke-width="2"/>
        <circle cx="335" cy="164" r="3" fill="#3aa55d"/>
      </g>
      <rect class="s-shadow2" x="302" y="297" width="112" height="5"/>
      <rect class="s-wood" x="300" y="290" width="112" height="7" rx="2"/>
      <g stroke="#9fb8d6" stroke-width="1.5">
        <rect x="306" y="274" width="62" height="16" rx="8" fill="#e8f1ff"/>
        <rect x="314" y="260" width="50" height="14" rx="7" fill="#f7f3e8"/>
      </g>
      <circle cx="392" cy="266" r="14" fill="#6fb7d9"/>
      <path d="M383 259 q7 -5 11 2 q-2 6 -9 4 z M394 271 q7 -2 9 3 q-5 5 -10 0 z" fill="#5fa36a"/>
      <path d="M392 280 v7 M383 289 h18" stroke="#7a5c44" stroke-width="3" stroke-linecap="round"/>
      <path class="s-metal" d="M80 444 L64 560 h8 L88 444 Z M112 444 L128 560 h8 L120 444 Z"/>
      <rect class="s-metal" x="70" y="516" width="62" height="5" rx="2"/>
      <g transform="rotate(-16 100 430)">
        <rect class="s-woodtop" x="44" y="408" width="112" height="46" rx="3"/>
        <rect x="52" y="413" width="92" height="34" rx="1" fill="#2f6fb3"/>
        <path d="M60 441 v-13 l12 -8 12 8 v13 M92 441 v-18 h12 v18 M112 431 h24" stroke="#e8f1ff" stroke-width="1.6" fill="none"/>
        <rect class="s-metal2" x="44" y="452" width="112" height="5" rx="2"/>
      </g>
      <path class="s-plant2" d="M400 542 Q378 526 380 500 Q394 516 400 536 Z"/>
      <path class="s-plant" d="M400 542 Q424 522 418 494 Q404 514 400 536 Z"/>
      <path class="s-plant" d="M400 542 Q390 510 402 486 Q410 512 400 542 Z"/>
      <path class="s-pot" d="M388 538 h24 l-3 24 h-18 z"/>
    </g>`;
  }

  function board() {
    return `<g>
      <rect class="s-shadow2" x="505" y="76" width="340" height="230" rx="8"/>
      <rect class="s-frame" x="500" y="70" width="340" height="230" rx="8"/>
      <rect class="s-board" x="507" y="77" width="326" height="216" rx="4"/>
      <text class="s-ink" x="670" y="100" text-anchor="middle" font-size="16" font-weight="800" letter-spacing="4">GÖREVLER</text>
      <path class="s-inkline" d="M716 282 l18 -15 16 8 22 -23 22 11 M796 259 l8 4 -5 6"/>
      <rect class="s-frame" x="560" y="298" width="220" height="10" rx="3"/>
      <rect x="592" y="291" width="26" height="7" rx="3" fill="#d64545"/>
      <rect x="624" y="291" width="26" height="7" rx="3" fill="#3d7cc9"/>
      <rect x="656" y="291" width="26" height="7" rx="3" fill="#3aa55d"/>
      <rect x="712" y="287" width="36" height="11" rx="2" fill="#5a4636"/><rect x="712" y="292" width="36" height="6" rx="1" fill="#d7d2c8"/>
    </g>`;
  }

  // "Önce güvenlik" afişi: felaket koruması ofisin kuralı
  function poster() {
    return `<g transform="rotate(1.5 907 150)">
      <rect class="s-shadow2" x="855" y="96" width="110" height="116" rx="3"/>
      <rect class="s-paper" x="852" y="92" width="110" height="116" rx="3"/>
      <path d="M852 116 V95 a3 3 0 0 1 3 -3 h104 a3 3 0 0 1 3 3 V116 Z" fill="#2f8a4d"/>
      <text x="907" y="109" text-anchor="middle" font-size="12" font-weight="800" letter-spacing="2" fill="#fff">ÖNCE</text>
      <path d="M881 170 Q881 138 907 136 Q933 138 933 170 Z" fill="#f5c400"/>
      <path d="M904 137 h6 v33 h-6 z" fill="#dcae00"/>
      <rect x="873" y="168" width="68" height="8" rx="4" fill="#e9b800"/>
      <text x="907" y="198" text-anchor="middle" font-size="12" font-weight="800" letter-spacing="1" fill="#2f8a4d">GÜVENLİK</text>
      <circle cx="907" cy="96" r="3" fill="#d64545"/>
    </g>`;
  }

  function extinguisher() {
    return `<g>
      <rect class="s-shadow2" x="452" y="458" width="28" height="4" rx="2"/>
      <rect x="455" y="408" width="22" height="52" rx="8" fill="#d64545"/>
      <rect x="460" y="400" width="12" height="10" rx="2" fill="#3a302a"/>
      <path d="M472 403 q12 2 9 18" stroke="#3a302a" stroke-width="3" fill="none" stroke-linecap="round"/>
      <rect x="458" y="424" width="16" height="13" rx="2" fill="#fff" fill-opacity=".85"/>
      <path d="M461 429 h10 M461 433 h7" stroke="#d64545" stroke-width="1.5"/>
    </g>`;
  }

  // Pencere: gündüz güneş ve bulutlar, gece ay, yıldızlar ve şehirde yanan ışıklar
  function windowView() {
    const stars = [[1008, 98], [1040, 124], [1070, 92], [1112, 132], [1128, 96], [1182, 150], [1016, 160], [1150, 170]]
      .map(([x, y], i) => `<circle class="s-star" cx="${x}" cy="${y}" r="${i % 3 ? 1.4 : 2}"/>`).join("");
    const lights = [[1000, 256], [1012, 266], [1046, 236], [1052, 250], [1098, 262], [1142, 244], [1146, 258], [1178, 262]]
      .map(([x, y]) => `<rect x="${x}" y="${y}" width="5" height="5"/>`).join("");
    return `<g>
      <rect class="s-shadow2" x="984" y="75" width="220" height="220" rx="6"/>
      <rect class="s-wood" x="980" y="70" width="220" height="220" rx="6"/>
      <rect x="990" y="80" width="200" height="200" fill="url(#g-sky)"/>
      <g clip-path="url(#c-win)">
        <circle class="s-sun" cx="1160" cy="110" r="18"/>
        <g class="s-night">${stars}
          <circle class="s-moon" cx="1160" cy="110" r="17"/><circle cx="1168" cy="104" r="14" fill="url(#g-sky)"/></g>
        <g class="s-drift">
          <path class="s-cloud" d="M1004 132 q0 -13 15 -13 q6 -12 20 -8 q11 -7 20 5 q14 0 14 16 z"/>
          <path class="s-cloud" d="M1096 206 q0 -11 13 -11 q6 -10 17 -6 q10 -5 16 5 q12 0 12 12 z"/>
          <path class="s-cloud" d="M1150 150 q0 -8 10 -8 q4 -7 12 -4 q8 -3 11 4 q9 0 9 8 z"/>
        </g>
        <path class="s-skyline" d="M990 280 V246 h18 v-14 h22 v22 h14 v-44 h26 v44 h18 v-22 h24 v22 h14 v-34 h28 v46 h18 v-24 h18 V280 Z"/>
        <g class="s-winlight">${lights}</g>
      </g>
      <rect class="s-wood" x="1087" y="80" width="6" height="200"/>
      <rect class="s-wood" x="990" y="177" width="200" height="6"/>
      <rect class="s-shadow2" x="972" y="301" width="236" height="6" rx="3"/>
      <rect class="s-woodtop" x="970" y="290" width="240" height="12" rx="3"/>
      <path class="s-plant" d="M1023 268 V246 q0 -9 7 -9 q7 0 7 9 V268 Z"/>
      <path class="s-plant" d="M1023 256 h-6 q-4 0 -4 -4 v-8 q0 -3 3 -3 q3 0 3 3 v5 h4 Z M1037 252 h6 q4 0 4 -4 v-6 q0 -3 -3 -3 q-3 0 -3 3 v3 h-4 Z"/>
      <path class="s-pot" d="M1016 266 h28 l-3 24 h-22 z"/>
    </g>`;
  }

  // Çay ocağı: duvar saati, raf, tabela, tezgâh, çaydanlık ve ince belli bardaklar
  function teaCorner() {
    let ticks = "";
    for (let i = 0; i < 12; i++) {
      const a = (i * Math.PI) / 6, r0 = i % 3 ? 26 : 22;
      ticks += `<line class="s-tick" x1="${r1(1330 + r0 * Math.sin(a))}" y1="${r1(170 - r0 * Math.cos(a))}" x2="${r1(1330 + 29 * Math.sin(a))}" y2="${r1(170 - 29 * Math.cos(a))}"/>`;
    }
    const glass = (x) => `
      <ellipse cx="${x}" cy="372" rx="11" ry="3" fill="#ece7df" stroke="#b9b2a8"/>
      <path class="s-glasscup" d="M${x - 8} 350 Q${x - 9} 356 ${x - 4.5} 360.5 Q${x - 8} 365 ${x - 7} 371 L${x + 7} 371 Q${x + 8} 365 ${x + 4.5} 360.5 Q${x + 9} 356 ${x + 8} 350 Z"/>
      <path class="s-tea" d="M${x - 7.3} 354 Q${x - 7.7} 357.5 ${x - 3.8} 360.8 Q${x - 7} 365 ${x - 6.2} 370.3 L${x + 6.2} 370.3 Q${x + 7} 365 ${x + 3.8} 360.8 Q${x + 7.7} 357.5 ${x + 7.3} 354 Z"/>`;
    return `<g>
      <circle class="s-shadow2" cx="1333" cy="174" r="36"/>
      <circle class="s-clockface" cx="1330" cy="170" r="34"/>
      <circle class="s-clockrim" cx="1330" cy="170" r="34"/>
      ${ticks}
      <line id="clock-h" class="s-hand" x1="1330" y1="173" x2="1330" y2="151" stroke-width="5"/>
      <line id="clock-m" class="s-hand" x1="1330" y1="174" x2="1330" y2="143" stroke-width="3.4"/>
      <line id="clock-s" x1="1330" y1="178" x2="1330" y2="144" stroke="#d64545" stroke-width="1.6" stroke-linecap="round"/>
      <circle cx="1330" cy="170" r="3.6" fill="#d64545"/>

      <rect class="s-shadow2" x="1402" y="238" width="160" height="5"/>
      <rect class="s-wood" x="1400" y="230" width="160" height="8" rx="2"/>
      <path class="s-wood2" d="M1414 238 v14 l10 -14 z M1546 238 v14 l-10 -14 z"/>
      <rect x="1412" y="196" width="30" height="34" rx="3" fill="#c0392b"/>
      <rect x="1410" y="192" width="34" height="7" rx="2" fill="#8e2a1f"/>
      <text x="1427" y="219" text-anchor="middle" font-size="9" font-weight="800" fill="#ffe7b0">ÇAY</text>
      <rect x="1452" y="204" width="26" height="26" rx="5" fill="#fff" fill-opacity=".55" stroke="#c9c2b8"/>
      <circle cx="1461" cy="220" r="5" fill="#d39b5a"/><circle cx="1469" cy="214" r="5" fill="#c4863f"/>
      <rect x="1449" y="200" width="32" height="6" rx="2" fill="#c9774f"/>
      <rect x="1486" y="210" width="24" height="20" rx="4" fill="#fff" fill-opacity=".55" stroke="#c9c2b8"/>
      <rect x="1491" y="220" width="6" height="6" fill="#fff"/><rect x="1499" y="218" width="6" height="6" fill="#fff"/>
      <path class="s-plant" d="M1527 214 q-12 22 -5 50 q7 -20 11 -48 z M1542 214 q12 26 3 56 q-7 -24 -7 -54 z"/>
      <path class="s-plant2" d="M1534 214 q-2 -14 8 -20 q2 12 -4 20 z"/>
      <path class="s-pot" d="M1522 212 h24 l-3 18 h-18 z"/>

      <path d="M1290 293 L1327 274 L1364 293" stroke="#6d625a" stroke-width="2" fill="none"/>
      <circle cx="1327" cy="273" r="3" fill="#6d625a"/>
      <rect class="s-sign" x="1262" y="292" width="130" height="30" rx="5"/>
      <text class="s-signink" x="1327" y="312.5" text-anchor="middle" font-size="15" font-weight="800" letter-spacing="1.5">ÇAY OCAĞI</text>

      <path class="s-plant2" d="M1215 440 Q1186 410 1190 370 Q1208 398 1215 430 Z"/>
      <path class="s-plant" d="M1215 440 Q1244 404 1238 364 Q1222 396 1215 430 Z"/>
      <path class="s-plant" d="M1215 440 Q1196 392 1214 348 Q1228 392 1215 440 Z"/>
      <path class="s-pot" d="M1199 436 h32 l-3 36 h-26 z"/>
      <rect class="s-shadow" x="1199" y="436" width="32" height="5"/>

      <rect class="s-shadow2" x="1236" y="478" width="348" height="8" rx="4"/>
      <rect class="s-counter" x="1244" y="386" width="332" height="94"/>
      <rect class="s-wood" x="1252" y="394" width="80" height="76" rx="3"/>
      <rect class="s-wood" x="1338" y="394" width="80" height="76" rx="3"/>
      <rect class="s-wood" x="1424" y="394" width="144" height="76" rx="3"/>
      <circle class="s-metal2" cx="1324" cy="432" r="3.5"/><circle class="s-metal2" cx="1346" cy="432" r="3.5"/>
      <rect class="s-woodedge" x="1244" y="472" width="332" height="8"/>
      <rect class="s-countertop" x="1240" y="374" width="340" height="12" rx="3"/>

      <path d="M1275 358 Q1275 374 1290 374 Q1305 374 1305 358 Z" fill="#fdfbf6" stroke="#c9c2b8"/>
      <path d="M1277 358 Q1290 349 1303 358 Z" fill="#fdfbf6" stroke="#c9c2b8"/>
      <circle cx="1290" cy="351" r="3" fill="#3d7cc9"/>
      <path d="M1280 366 q10 4 20 0" stroke="#3d7cc9" stroke-width="1.5" fill="none"/>

      <path d="M1498 336 Q1522 340 1514 366" stroke="#6f7880" stroke-width="5" fill="none" stroke-linecap="round"/>
      <path class="s-metal" d="M1440 350 Q1422 346 1416 330 L1422 327 Q1428 340 1442 342 Z"/>
      <path class="s-metal2" d="M1436 374 Q1430 344 1448 328 L1492 328 Q1510 344 1504 374 Z"/>
      <path d="M1442 360 q28 8 56 0" stroke="#fff" stroke-opacity=".5" stroke-width="3" fill="none"/>
      <rect class="s-metal" x="1444" y="322" width="52" height="7" rx="3"/>
      <path d="M1486 296 q14 2 10 20" stroke="#b83c3c" stroke-width="4" fill="none" stroke-linecap="round"/>
      <path d="M1454 308 Q1440 304 1436 292 L1441 290 Q1446 300 1456 302 Z" fill="#b83c3c"/>
      <path d="M1452 322 Q1446 300 1460 290 L1480 290 Q1494 300 1488 322 Z" fill="#d94f4f"/>
      <path d="M1456 306 q14 6 28 0" stroke="#fff" stroke-width="2" fill="none" stroke-opacity=".75"/>
      <rect x="1458" y="285" width="24" height="6" rx="3" fill="#b83c3c"/>
      <circle cx="1470" cy="282" r="4" fill="#b83c3c"/>
      <path class="s-steam" d="M1466 272 q-5 -8 0 -15 q5 -7 0 -14"/>
      <path class="s-steam b" d="M1477 274 q-5 -8 0 -15 q5 -7 0 -14"/>
      ${glass(1532)}${glass(1560)}
    </g>`;
  }

  const STICKER = {
    sol: (x, y) => `<circle cx="${x}" cy="${y}" r="10" fill="#f5c400"/>
      <path d="M${x - 7} ${y + 3} q0 -9 7 -9 q7 0 7 9 z" fill="#fff"/><rect x="${x - 8.5}" y="${y + 2}" width="17" height="3" rx="1.5" fill="#fff"/>`,
    sonnet: (x, y) => `<circle cx="${x}" cy="${y}" r="10" fill="#3d7cc9"/>
      <path d="M${x - 2.5} ${y + 4} v-10 l7 -2 v9" stroke="#fff" stroke-width="2" fill="none"/>
      <circle cx="${x - 4.5}" cy="${y + 4.5}" r="2.6" fill="#fff"/><circle cx="${x + 2.5}" cy="${y + 2.5}" r="2.6" fill="#fff"/>`,
    luna: (x, y) => `<circle cx="${x}" cy="${y}" r="10" fill="#3aa55d"/><path d="${starPath(x, y + 0.6, 6.8, 2.9)}" fill="#fff"/>`,
    uzman: (x, y) => `<circle cx="${x}" cy="${y}" r="10" fill="#b04fc9"/>
      <circle cx="${x}" cy="${y - 1.5}" r="4.6" fill="#fff"/><rect x="${x - 2.5}" y="${y + 2}" width="5" height="4.5" rx="1" fill="#fff"/>`,
  };
  const MUG = { sol: "#ef8f1f", sonnet: "#3d7cc9", luna: "#3aa55d", uzman: "#b04fc9" };
  // Monitörle çalışan arasındaki boşlukta masaya özgü küçük eşya
  const EXTRA = {
    sol: (cx) => `<rect x="${cx - 54}" y="670" width="22" height="18" rx="5" fill="#f5c400"/>
      <circle cx="${cx - 43}" cy="679" r="4.5" fill="#3a302a"/><rect x="${cx - 34}" y="684" width="9" height="3" fill="#e9b800"/>`,
    sonnet: (cx) => `<path d="M${cx - 46} 666 l-3 -14" stroke="#ffc83d" stroke-width="3" stroke-linecap="round"/>
      <path d="M${cx - 41} 666 l1 -16" stroke="#e05252" stroke-width="3" stroke-linecap="round"/>
      <path d="M${cx - 36} 666 l4 -12" stroke="#3aa55d" stroke-width="3" stroke-linecap="round"/>
      <rect x="${cx - 50}" y="664" width="18" height="24" rx="3" fill="#3d7cc9"/>`,
    luna: (cx) => `<rect x="${cx - 56}" y="680" width="30" height="8" rx="1" fill="#e05252"/>
      <rect x="${cx - 53}" y="672" width="26" height="8" rx="1" fill="#3aa55d"/>
      <rect x="${cx - 55}" y="664" width="28" height="8" rx="1" fill="#ffc83d"/>`,
    uzman: (cx) => `<path class="s-plant" d="M${cx - 40} 674 q-10 -4 -8 -14 q6 4 8 14 z M${cx - 40} 674 q10 -4 8 -14 q-6 4 -8 14 z M${cx - 40} 674 q-2 -10 0 -18 q2 8 0 18 z"/>
      <path class="s-pot" d="M${cx - 50} 672 h20 l-2 16 h-16 z"/>`,
  };

  function monitor(cx, key) {
    const mx = cx - 105;
    return `
      <rect class="s-metal" x="${mx - 5}" y="660" width="10" height="20"/>
      <ellipse class="s-metal" cx="${mx}" cy="683" rx="24" ry="4.5"/>
      <rect class="s-metal2" x="${cx - 150}" y="598" width="90" height="64" rx="7"/>
      <rect class="s-metal" x="${cx - 138}" y="607" width="66" height="46" rx="5" opacity=".3"/>
      ${STICKER[key](mx, 628)}
      <circle class="s-led" cx="${cx - 69}" cy="654" r="2.6"/>`;
  }

  function laptop(cx, key) {
    return `
      <path class="s-metal2" d="M${cx - 138} 630 Q${cx - 138} 626 ${cx - 134} 626 H${cx - 56} Q${cx - 52} 626 ${cx - 52} 630 L${cx - 49} 684 H${cx - 141} Z"/>
      ${STICKER[key](cx - 95, 652)}
      <circle class="s-led" cx="${cx - 60}" cy="677" r="2.4"/>`;
  }

  // Masanın arka tarafı: ekran ışığı, sandalye sırtı, monitörün arkası (Sol'da dizüstü), kupa
  function deskBack(d) {
    const { cx, key } = d;
    return `<g id="desk-${key}" class="desk" data-busy="0">
      <ellipse class="s-glow" cx="${cx - 105}" cy="632" rx="95" ry="70" fill="url(#g-screen)"/>
      <rect class="s-chair" x="${cx + 9}" y="624" width="52" height="66" rx="12"/>
      <rect class="s-chair2" x="${cx + 15}" y="630" width="40" height="52" rx="9"/>
      ${key === "sol" ? laptop(cx, key) : monitor(cx, key)}
      ${EXTRA[key](cx)}
      <path d="M${cx + 134} 673 q9 0 9 7 q0 7 -9 7" stroke="#d9d2c6" stroke-width="3.5" fill="none"/>
      <rect class="s-mug" x="${cx + 116}" y="667" width="19" height="22" rx="3"/>
      <rect x="${cx + 116}" y="673" width="19" height="5" fill="${MUG[key]}"/>
    </g>`;
  }

  // Masanın önü: çekmeceler, ayak, tabla ve klavye; karakterin alt yarısını örter
  function deskFront(d) {
    const { cx } = d;
    let handles = "";
    for (const y of [719, 766, 813]) handles += `<rect class="s-metal2" x="${cx - 107}" y="${y}" width="28" height="6" rx="3"/>`;
    return `<g>
      <ellipse class="s-shadow" cx="${cx}" cy="841" rx="168" ry="9"/>
      <rect class="s-wood2" x="${cx - 140}" y="698" width="280" height="90"/>
      <rect class="s-wood" x="${cx - 147}" y="698" width="107" height="140" rx="3"/>
      <path class="s-woodline" d="M${cx - 147} 745 h107 M${cx - 147} 792 h107"/>
      ${handles}
      <rect class="s-wood" x="${cx + 134}" y="698" width="13" height="140" rx="2"/>
      <rect class="s-woodtop" x="${cx - 156}" y="684" width="312" height="16" rx="4"/>
      <rect class="s-woodedge" x="${cx - 156}" y="696" width="312" height="4" rx="2"/>
      <rect class="s-kb" x="${cx + 5}" y="682" width="60" height="9" rx="2"/>
      <path class="s-kbline" d="M${cx + 9} 685.5 h52 M${cx + 9} 688.5 h52"/>
    </g>`;
  }

  function frontPlants() {
    const plant = (x, flip) => `<g transform="translate(${x} 0)${flip ? " scale(-1 1)" : ""}">
      <path class="s-plant2" d="M0 842 Q-38 806 -30 740 Q-10 792 0 834 Z"/>
      <path class="s-plant" d="M0 842 Q32 790 22 718 Q6 786 0 834 Z"/>
      <path class="s-plant" d="M0 846 Q-18 772 -2 700 Q12 774 0 846 Z"/>
      <path class="s-plant2" d="M0 846 Q-30 820 -44 790 Q-14 800 0 836 Z"/>
      <path class="s-pot" d="M-24 836 H24 L19 900 H-19 Z"/>
      <rect class="s-shadow" x="-24" y="836" width="48" height="6"/>
    </g>`;
    return plant(30, false) + plant(1572, true);
  }

  function back() {
    return svg("back", defs() + wall() + cones() + room() + board() + poster() + extinguisher() + windowView()
      + teaCorner() + DESKS.map(deskBack).join("") + lamps());
  }

  function glass() {
    return svg("glass", `
      <rect class="s-glass" x="30" y="60" width="400" height="515"/>
      <path class="s-ghi" d="M44 336 L114 224 L128 224 L58 336 Z M68 352 L122 266 L128 266 L74 352 Z M346 300 L414 190 L426 190 L358 300 Z M362 318 L418 228 L423 228 L367 318 Z"/>
      <rect class="s-frost" x="33" y="250" width="394" height="12"/>
      <path class="s-gbar" d="M140 60 V575 M320 60 V575"/>
      <rect class="s-gframe" x="30" y="60" width="400" height="515" rx="3"/>
      <rect class="s-gsill" x="24" y="54" width="412" height="9" rx="2"/>
      <rect class="s-gsill" x="24" y="571" width="412" height="9" rx="2"/>
      <rect class="s-metal" x="328" y="438" width="7" height="60" rx="3.5"/>`);
  }

  function front() {
    return svg("front", DESKS.map(deskFront).join("") + frontPlants());
  }

  window.JevScene = { W, H, DESKS, SPOTS, PLATES, AISLE, ROOM, back, glass, front, backdrop };
})();
