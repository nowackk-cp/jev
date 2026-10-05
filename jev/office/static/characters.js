/* Jev ofisi karakterleri: her ajan tatlı bir işçi.
 * JevChars.build(ad) tam boy figürün SVG dizesini, JevChars.head(ad) kanban kartları için küçük kafayı döndürür.
 * SVG'lerde kimlik (id) yoktur; aynı figür sayfada birden çok kez kullanılabilir.
 * Duruş, yüz ifadesi ve eşyalar CSS ile açılıp kapanır (office.css: data-arms, data-prop, data-eyes, data-mouth, data-fx).
 */
(function () {
  "use strict";

  const META = {
    opus: { title: "Proje şefi", color: "#b04fc9" },
    sol: { title: "Kıdemli usta", color: "#e6b400" },
    sonnet: { title: "Usta", color: "#3d7cc9" },
    luna: { title: "Çırak", color: "#3aa55d" },
    jev: { title: "Ustabaşı", color: "#e07a2e" },
  };

  const LOOK = {
    opus: { skin: "#eab893", skin2: "#d59a73", hair: "#9d99a5", brow: "#77737f", browW: 3.4, top: "#b04fc9",
            top2: "#8a3aa0", pants: "#4a4458", shoe: "#3b2f2a" },
    sol: { skin: "#d8a07a", skin2: "#bf835d", hair: "#5b3a24", top: "#4f78a8", top2: "#3e6390",
           pants: "#9a8866", shoe: "#4e3525" },
    sonnet: { skin: "#f6d2b3", skin2: "#e6b28f", hair: "#a3482b", top: "#f7f5f0", top2: "#e3dfd6", short: true,
              pants: "#3d7cc9", shoe: "#f3f3f3" },
    luna: { skin: "#f1c59e", skin2: "#dda57c", hair: "#b8773a", top: "#43b06a", top2: "#338f53", short: true,
            pants: "#35507a", shoe: "#f4f6f2" },
    jev: { skin: "#c98f66", skin2: "#ae734c", hair: "#23232b", browW: 4, top: "#34495e", top2: "#293b4d",
           pants: "#2e3a4f", shoe: "#26262b" },
    _: { skin: "#eec3a0", skin2: "#d9a683", hair: "#4a3b33", top: "#8a8f98", top2: "#6f747c",
         pants: "#454a52", shoe: "#3a3330" },
  };

  // Kol duruşları: [omuzdan ele yol, el x, el y] × (sol kol, sağ kol)
  const ARMS = {
    rest: [["M54 110 Q45 130 47 150", 47, 150], ["M106 110 Q115 130 113 150", 113, 150]],
    hold: [["M54 110 Q50 134 68 139", 68, 139], ["M106 110 Q110 134 92 139", 92, 139]],
    type: [["M54 110 Q52 138 63 148", 63, 148], ["M106 110 Q108 138 97 148", 97, 148]],
    up: [["M54 110 Q45 130 47 150", 47, 150], ["M106 110 Q130 96 130 60", 130, 60]],
    cheer: [["M54 110 Q30 96 30 60", 30, 60], ["M106 110 Q130 96 130 60", 130, 60]],
  };

  const HARD_HAT = (fill, ridge, brim, band) => `
    <path class="o" d="M38 54 Q38 16 80 14 Q122 16 122 54 Z" fill="${fill}"/>
    <path d="M75 15 Q80 13 85 15 L86 50 L74 50 Z" fill="${ridge}"/>
    ${band ? `<path d="M39 47 Q80 39 121 47 L121.6 52 Q80 44 38.4 52 Z" fill="${band}"/>` : ""}
    <path class="o" d="M31 53 Q80 45 129 53 Q130 59 124 59 Q80 52 36 59 Q30 59 31 53 Z" fill="${brim}"/>
    <path d="M50 30 Q58 21 70 19" stroke="#fff" stroke-opacity=".55" stroke-width="3.5" fill="none" stroke-linecap="round"/>`;

  function legs(c) {
    return `
      <g class="c-leg c-leg-l"><rect class="o" x="62" y="156" width="15" height="34" rx="6" fill="${c.pants}"/>
        <rect class="o" x="56" y="186" width="23" height="10" rx="5" fill="${c.shoe}"/></g>
      <g class="c-leg c-leg-r"><rect class="o" x="83" y="156" width="15" height="34" rx="6" fill="${c.pants}"/>
        <rect class="o" x="81" y="186" width="23" height="10" rx="5" fill="${c.shoe}"/></g>`;
  }

  function back(name) {
    if (name === "luna") {  // turuncu sırt çantası
      return `<rect class="o" x="41" y="106" width="78" height="58" rx="15" fill="#f08a24"/>
        <rect x="47" y="150" width="66" height="10" rx="5" fill="#d9731a"/>`;
    }
    return "";
  }

  function torso(c) {
    return `<path class="o" d="M50 172 C48 142 50 118 58 108 Q66 101 80 101 Q94 101 102 108 C110 118 112 142 110 172 Q80 177 50 172 Z" fill="${c.top}"/>
      <ellipse cx="80" cy="106" rx="19" ry="5" fill="#000" fill-opacity=".12"/>`;
  }

  function outfit(name, c) {
    switch (name) {
      case "opus": return `
        <path d="M70 102 L90 102 L88 173 L72 173 Z" fill="#f1e8f6"/>
        <path d="M70 102 L78 113 L71 116 Z M90 102 L82 113 L89 116 Z" fill="#fff"/>
        <path d="M71 104 L73 173 M89 104 L87 173" stroke="${c.top2}" stroke-width="3"/>
        <circle cx="74" cy="128" r="1.8" fill="#f1e8f6"/><circle cx="74" cy="146" r="1.8" fill="#f1e8f6"/>
        <rect x="54" y="150" width="14" height="12" rx="2" fill="${c.top2}"/><rect x="92" y="150" width="14" height="12" rx="2" fill="${c.top2}"/>
        <path d="M72 104 L77 131 M88 104 L83 131" stroke="#3d7cc9" stroke-width="2" fill="none"/>
        <rect x="73.5" y="130" width="13" height="16" rx="1.5" fill="#fff" stroke="#c9c2d4"/>
        <rect x="73.5" y="130" width="13" height="4" fill="${c.top}"/>
        <g class="c-clip" transform="rotate(-8 45 133)">
          <rect x="33" y="116" width="23" height="32" rx="2.5" fill="#b9885d" class="o"/>
          <rect x="36" y="121" width="17" height="24" fill="#fff"/>
          <rect x="40" y="113" width="9" height="5" rx="1.5" fill="#8a8f96"/>
          <rect x="38" y="126" width="9" height="9" fill="#ffd84d"/></g>`;
      case "sol": return `
        <path d="M67 101 L80 111 L93 101 L95 109 L80 118 L65 109 Z" fill="#6d93c0"/>
        <rect x="57" y="118" width="14" height="12" rx="1.5" fill="${c.top2}"/><rect x="89" y="118" width="14" height="12" rx="1.5" fill="${c.top2}"/>
        <path d="M57 118 h14 v4 h-14z M89 118 h14 v4 h-14z" fill="#6d93c0"/>
        <circle cx="80" cy="126" r="1.7" fill="#dfe8f2"/><circle cx="80" cy="138" r="1.7" fill="#dfe8f2"/>
        <rect x="49" y="148" width="62" height="9" rx="2" fill="#7a4a26"/>
        <rect x="75" y="147.5" width="10" height="10" rx="1.5" fill="#d4b04c"/>
        <path d="M50 156 h16 v13 q-8 4 -16 0z M94 156 h16 v13 q-8 4 -16 0z" fill="#8c5a30"/>
        <g transform="rotate(15 106 153)"><rect x="104" y="142" width="4" height="15" fill="#c98f4f"/>
          <rect x="99.5" y="139" width="13" height="5.5" rx="1" fill="#8a8f96"/></g>
        <circle cx="58" cy="162" r="4.6" fill="#f2c200" stroke="#b58f00" stroke-width="1.2"/>`;
      case "sonnet": return `
        <path d="M50 146 L110 146 L111 172 Q80 177 49 172 Z" fill="${c.pants}"/>
        <rect x="62" y="119" width="36" height="30" rx="3" fill="${c.pants}"/>
        <rect x="72" y="127" width="16" height="11" rx="2" fill="#346db3"/>
        <path d="M58 103 L65 121 M102 103 L95 121" stroke="${c.pants}" stroke-width="6" stroke-linecap="round"/>
        <circle cx="65" cy="122" r="2.8" fill="#ffd34d"/><circle cx="95" cy="122" r="2.8" fill="#ffd34d"/>
        <path d="M80 150 V172" stroke="#346db3" stroke-width="1.5"/>`;
      case "luna": return `
        <path d="M57 104 Q54 130 57 152 M103 104 Q106 130 103 152" stroke="#f08a24" stroke-width="7" fill="none" stroke-linecap="round"/>
        <rect x="52" y="128" width="9" height="6" rx="1.5" fill="#c96a14"/><rect x="99" y="128" width="9" height="6" rx="1.5" fill="#c96a14"/>
        <path d="M72 102 Q80 109 88 102" stroke="${c.top2}" stroke-width="2.5" fill="none"/>
        <circle cx="80" cy="134" r="6" fill="#fff" fill-opacity=".85"/><path d="M77 134 l2 2 4 -4" stroke="${c.top}" stroke-width="1.8" fill="none"/>`;
      case "jev": return `
        <path d="M50 172 C48 142 50 118 58 108 Q64 103 72 102 L74 174 Q60 174 50 172 Z" fill="#f08a3c"/>
        <path d="M110 172 C112 142 110 118 102 108 Q96 103 88 102 L86 174 Q100 174 110 172 Z" fill="#f08a3c"/>
        <path d="M49.5 140 h24.2 v5.5 h-24.2z M86.3 140 h24.2 v5.5 h-24.2z M49.5 157 h24.3 v5.5 h-24.3z M86.2 157 h24.3 v5.5 h-24.3z" fill="#e3e9ee"/>
        <path d="M61 106 L62.5 140 L68 140 L66.5 104 Z M99 106 L97.5 140 L92 140 L93.5 104 Z" fill="#e3e9ee"/>
        <path d="M74 104 V172 M86 104 V172" stroke="#c9651f" stroke-width="1.5"/>
        <g class="c-radio"><rect x="92" y="111" width="10" height="17" rx="2" fill="#2b2b30"/>
          <rect x="99" y="102" width="2.5" height="10" rx="1" fill="#2b2b30"/><rect x="94" y="114" width="6" height="4" fill="#7fd1a0"/>
          <circle cx="97" cy="123" r="1.6" fill="#555"/></g>`;
      default: return "";
    }
  }

  function heldProps() {
    return `
    <g class="c-p c-p-tea">
      <ellipse cx="80" cy="142" rx="13" ry="3.4" fill="#ece7df" stroke="#b9b2a8"/>
      <path d="M72 120 Q71 126 75.5 130.5 Q72 135 73 141 L87 141 Q88 135 84.5 130.5 Q89 126 88 120 Z" fill="#fff" fill-opacity=".55" stroke="#c9c2b8"/>
      <path d="M72.7 124 Q72.3 127.5 76.2 130.8 Q73 135 73.8 140.3 L86.2 140.3 Q87 135 83.8 130.8 Q87.7 127.5 87.3 124 Z" fill="#b5451b"/>
      <path d="M74 124.6 h12" stroke="#e2733f" stroke-width="1.3"/>
      <path class="c-steam" d="M76 116 q-3 -5 0 -9 q3 -4 0 -8 M84 116 q-3 -5 0 -9 q3 -4 0 -8" stroke="#fff" stroke-opacity=".8" stroke-width="2" fill="none" stroke-linecap="round"/>
    </g>
    <g class="c-p c-p-page" transform="rotate(-4 80 129)">
      <rect x="64" y="113" width="32" height="31" rx="2" fill="#fff" stroke="#c9c2b8"/>
      <path d="M69 120 h22 M69 125.5 h22 M69 131 h15 M69 136.5 h19" stroke="#9aa4ae" stroke-width="1.6"/>
    </g>
    <g class="c-p c-p-drawing">
      <rect x="59" y="112" width="42" height="31" rx="2" fill="#2f6fb3" stroke="#1f4f86"/>
      <path d="M59 122 h42 M59 132 h42 M73 112 v31 M87 112 v31" stroke="#fff" stroke-opacity=".15"/>
      <path d="M64 138 v-11 l9 -7 9 7 v11 z M86 138 v-15 h9 v15 M69 138 v-6 h5 v6" stroke="#e8f1ff" stroke-width="1.4" fill="none"/>
      <g transform="rotate(-42 94 136)"><rect x="90" y="134" width="20" height="4.2" rx="1" fill="#ffc83d"/>
        <path d="M110 134 l4.5 2.1 -4.5 2.1z" fill="#f2d2a9"/><rect x="88" y="134" width="3" height="4.2" fill="#f28c8c"/></g>
    </g>
    <g class="c-p c-p-notes">
      <rect x="61" y="117" width="19" height="19" fill="#ffd84d" transform="rotate(-11 70 126)"/>
      <rect x="71" y="113" width="19" height="19" fill="#ff9ecb" transform="rotate(4 80 122)"/>
      <rect x="80" y="118" width="19" height="19" fill="#9fe0a8" transform="rotate(13 89 127)"/>
      <path d="M75 118 h11 M75 122 h8 M83 124 h10" stroke="#00000033" stroke-width="1.4"/>
    </g>
    <g class="c-p c-p-clipboard">
      <rect class="o" x="63" y="109" width="34" height="40" rx="3" fill="#b9885d"/>
      <rect x="66.5" y="115" width="27" height="31" fill="#fff"/>
      <rect x="74" y="106" width="12" height="6.5" rx="1.5" fill="#8a8f96"/>
      <path d="M69.5 121 l2.5 2.5 4 -5 M69.5 129.5 l2.5 2.5 4 -5 M69.5 138 l2.5 2.5 4 -5" stroke="#3a9d5d" stroke-width="1.9" fill="none" stroke-linecap="round"/>
      <path d="M79 121 h11 M79 129.5 h11 M79 138 h8" stroke="#9aa4ae" stroke-width="1.5"/>
    </g>
    <g class="c-p c-p-report">
      <rect x="62" y="109" width="36" height="40" rx="2" fill="#fffdf6" stroke="#c9c2b8"/>
      <rect x="66.5" y="114" width="27" height="4.5" rx="1" fill="#1fa5b8"/>
      <path d="M66.5 124 h27 M66.5 129 h27 M66.5 134 h18" stroke="#9aa4ae" stroke-width="1.5"/>
      <path d="M86 142 l-3 9 4 -2 3 3 1 -9z M94 142 l3 9 -4 -2 -3 3 -1 -9z" fill="#d64545"/>
      <circle cx="90" cy="141" r="5.5" fill="#e0a526" stroke="#b9851a"/>
    </g>
    <g class="c-p c-p-keyboard">
      <rect class="o" x="56" y="140" width="48" height="10" rx="2" fill="#e9e9ee"/>
      <path d="M60 143.5 h40 M60 147 h40" stroke="#a8a8b4" stroke-width="1.3" stroke-dasharray="3 1.6"/>
    </g>
    <g class="c-p c-p-magnifier">
      <path d="M131 62 L133 50" stroke="#5b4a3a" stroke-width="5" stroke-linecap="round"/>
      <circle cx="135" cy="37" r="12" fill="#cfe8ff" fill-opacity=".55" stroke="#5b4a3a" stroke-width="3.6"/>
      <path d="M128 33 q3 -5 8 -5" stroke="#fff" stroke-width="2.2" fill="none" stroke-linecap="round"/>
    </g>
    <g class="c-p c-p-tube" transform="rotate(14 130 45)">
      <rect x="125.5" y="22" width="9" height="36" rx="4.5" fill="#fff" fill-opacity=".75" stroke="#8aa3ad" stroke-width="1.5"/>
      <path d="M126.3 39 h7.4 v14.5 a3.7 3.7 0 0 1 -7.4 0z" fill="#3aa55d"/>
      <circle class="c-bub" cx="129" cy="35" r="1.5" fill="#3aa55d"/><circle class="c-bub b2" cx="131.5" cy="31" r="1.1" fill="#3aa55d"/>
    </g>`;
  }

  function arms(c) {
    const col = c.short ? c.skin : c.top;
    let out = "";
    for (const k of Object.keys(ARMS)) {
      const [[dl, lx, ly], [dr, rx, ry]] = ARMS[k];
      out += `<g class="c-arms c-arms-${k}">
        <path d="${dl}" stroke="#000" stroke-opacity=".22" stroke-width="16" stroke-linecap="round" fill="none"/>
        <path d="${dr}" stroke="#000" stroke-opacity=".22" stroke-width="16" stroke-linecap="round" fill="none"/>
        <path d="${dl}" stroke="${col}" stroke-width="13" stroke-linecap="round" fill="none"/>
        <path d="${dr}" stroke="${col}" stroke-width="13" stroke-linecap="round" fill="none"/>
        <circle class="o h-l" cx="${lx}" cy="${ly}" r="6.5" fill="${c.skin}"/>
        <circle class="o h-r" cx="${rx}" cy="${ry}" r="6.5" fill="${c.skin}"/></g>`;
    }
    if (c.short) {
      out += `<ellipse class="o" cx="55" cy="111" rx="9.5" ry="8.5" fill="${c.top}"/>
        <ellipse class="o" cx="105" cy="111" rx="9.5" ry="8.5" fill="${c.top}"/>`;
    }
    return out;
  }

  function hairBack(name, c) {
    switch (name) {
      case "sonnet": return `<path d="M38 58 Q33 84 45 94 L49 68 Z M122 58 Q127 84 115 94 L111 68 Z" fill="${c.hair}"/>`;
      case "luna": return `<path class="o c-pony" d="M45 44 Q17 40 14 68 Q13 92 27 106 Q24 84 34 72 Q40 62 47 56 Z" fill="${c.hair}"/>
        <circle cx="44" cy="50" r="4.5" fill="#f08a24"/>`;
      default: return "";
    }
  }

  function face(name, c) {
    const brow = c.brow || c.hair;
    const bw = c.browW || 2.6;
    const mouthShift = name === "jev" ? ` transform="translate(0 4)"` : "";
    let extra = "";
    if (name === "opus") {
      extra = `<path d="M57 78 h15 q-1 6.5 -7.5 6.5 q-6.5 0 -7.5 -6.5z M88 78 h15 q-1 6.5 -7.5 6.5 q-6.5 0 -7.5 -6.5z" fill="#fff" fill-opacity=".25" stroke="#5b4a66" stroke-width="2"/>
        <path d="M72 79 q8 -3 16 0" stroke="#5b4a66" stroke-width="2" fill="none"/>`;
    } else if (name === "sol") {
      extra = `<path d="M59 93 Q63 109 80 110 Q97 109 101 93 Q91 100 80 100 Q69 100 59 93 Z" fill="${c.hair}"/>
        <path d="M69 86 Q80 80 91 86 Q86 89 80 87.5 Q74 89 69 86 Z" fill="${c.hair}"/>`;
    } else if (name === "jev") {
      extra = `<path d="M63 86 Q71 78.5 80 83 Q89 78.5 97 86 Q92 91.5 84 88.5 Q80 87 76 88.5 Q68 91.5 63 86 Z" fill="${c.hair}"/>`;
    } else if (name === "luna") {
      extra = `<g fill="#c77b4e" fill-opacity=".55"><circle cx="54" cy="80" r="1.1"/><circle cx="58" cy="83" r="1.1"/>
        <circle cx="52" cy="84" r="1.1"/><circle cx="106" cy="80" r="1.1"/><circle cx="102" cy="83" r="1.1"/><circle cx="108" cy="84" r="1.1"/></g>`;
    }
    return `
      <g class="c-brow-n" stroke="${brow}" stroke-width="${bw}" stroke-linecap="round" fill="none">
        <path d="M60 60 q6 -4 12 0"/><path d="M88 60 q6 -4 12 0"/></g>
      <g class="c-brow-w" stroke="${brow}" stroke-width="${bw}" stroke-linecap="round" fill="none">
        <path d="M60 61 q6 -1 12 -4.5"/><path d="M88 56.5 q6 3.5 12 4.5"/></g>
      <g class="c-eye-open">
        <ellipse cx="66" cy="72" rx="4.2" ry="5.6" fill="#2b2622"/><circle cx="67.6" cy="70" r="1.5" fill="#fff"/>
        <ellipse cx="94" cy="72" rx="4.2" ry="5.6" fill="#2b2622"/><circle cx="95.6" cy="70" r="1.5" fill="#fff"/></g>
      <g class="c-eye-closed" stroke="#2b2622" stroke-width="2.4" fill="none" stroke-linecap="round">
        <path d="M61 73 q5 4 10 0"/><path d="M89 73 q5 4 10 0"/></g>
      <g class="c-eye-happy" stroke="#2b2622" stroke-width="2.6" fill="none" stroke-linecap="round">
        <path d="M61 75 q5 -7 10 0"/><path d="M89 75 q5 -7 10 0"/></g>
      <ellipse cx="80" cy="80" rx="2.4" ry="1.7" fill="${c.skin2}"/>
      <ellipse class="c-blush" cx="57" cy="85" rx="6.2" ry="3.4"/><ellipse class="c-blush" cx="103" cy="85" rx="6.2" ry="3.4"/>
      <g class="c-mouth"${mouthShift}>
        <path class="m-smile" d="M73 88 q7 6 14 0" stroke="#7a3b2e" stroke-width="2.4" fill="none" stroke-linecap="round"/>
        <ellipse class="m-o" cx="80" cy="91" rx="3.6" ry="4.4" fill="#7a3b2e"/>
        <g class="m-open"><path d="M71 87 q9 13 18 0 z" fill="#7a3b2e"/><path d="M75 93 q5 3.5 10 0 q-5 -2.5 -10 0z" fill="#e8747c"/></g>
      </g>
      ${extra}`;
  }

  function hairFront(name, c) {
    switch (name) {
      case "opus": return `<path class="o" d="M40 67 Q37 31 72 24 Q105 19 118 43 Q123 54 120 67 Q116 50 104 44 Q92 39 70 44 Q54 48 46 60 Q42 64 40 67 Z" fill="${c.hair}"/>
        <path d="M54 37 q14 -9 32 -9 M92 30 q14 2 20 12" stroke="#d9d6de" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M63 29 q-9 10 -15 26" stroke="#7a7682" stroke-width="2" fill="none"/>
        <path d="M41 60 L46 60 L46 76 Q42 72 41 68 Z M119 60 L114 60 L114 76 Q118 72 119 68 Z" fill="${c.hair}"/>`;
      case "sol": return `<path d="M43 54 L50 54 L50 77 Q46 75 43 70 Z M117 54 L110 54 L110 77 Q114 75 117 70 Z" fill="${c.hair}"/>`;
      case "sonnet": return `<path class="o" d="M40 63 Q35 37 53 28 Q60 15 76 20 Q88 11 101 22 Q119 24 121 46 Q124 56 120 64 Q112 48 97 45 Q86 51 71 45 Q55 47 46 59 Z" fill="${c.hair}"/>
        <path d="M58 32 q8 -6 16 -4 M86 22 q10 0 16 8" stroke="#c9653f" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M40 70 Q37 23 80 21 Q123 23 120 70" stroke="#444a55" stroke-width="5.5" fill="none"/>
        <rect class="o" x="30" y="59" width="14" height="24" rx="6.5" fill="#3d7cc9"/><rect x="34" y="63" width="6" height="16" rx="3" fill="#2b5e9e"/>
        <rect class="o" x="116" y="59" width="14" height="24" rx="6.5" fill="#3d7cc9"/><rect x="120" y="63" width="6" height="16" rx="3" fill="#2b5e9e"/>
        <g transform="rotate(-38 124 48)"><rect x="113" y="45.5" width="22" height="5" rx="1" fill="#ffc83d"/>
          <path d="M135 45.5 l5 2.5 -5 2.5z" fill="#f2d2a9"/><rect x="111" y="45.5" width="3" height="5" fill="#f28c8c"/></g>`;
      case "luna": return `<path d="M43 56 Q52 64 60 57 Q68 65 78 58 L78 54 L43 54 Z" fill="${c.hair}"/>
        <path d="M42 58 L48 58 L47 76 Q43 72 42 66 Z" fill="${c.hair}"/>`;
      case "jev": return `<path class="o" d="M40 65 Q38 29 80 24 Q122 29 120 65 Q116 45 104 40 Q92 34 80 36 Q68 34 56 40 Q44 46 40 65 Z" fill="${c.hair}"/>
        <path d="M58 30 l6 -6 4 5 5 -7 4 6 6 -6 3 7" stroke="${c.hair}" stroke-width="5" fill="none" stroke-linejoin="round"/>
        <path d="M41 60 L46 60 L46 76 Q42 72 41 68 Z M119 60 L114 60 L114 76 Q118 72 119 68 Z" fill="${c.hair}"/>`;
      default: return `<path class="o" d="M40 64 Q38 28 80 25 Q122 28 120 64 Q110 44 80 42 Q50 44 40 64 Z" fill="${c.hair}"/>`;
    }
  }

  function hat(name) {
    if (name === "sol") return HARD_HAT("#f5c400", "#dcae00", "#e9b800", "#ef8f1f");
    if (name === "luna") {
      return `<path class="o" d="M40 57 Q40 22 80 20 Q120 22 120 57 Q80 50 40 57 Z" fill="#3aa55d"/>
        <path d="M80 21 L80 53 M60 25 Q56 38 57 54 M100 25 Q104 38 103 54" stroke="#2f8a4d" stroke-width="1.6" fill="none"/>
        <circle cx="80" cy="21" r="3.4" fill="#2f8a4d"/>
        <path class="o" d="M86 50 Q114 42 139 52 Q137 58 127 58 Q106 56 88 56 Z" fill="#2f8a4d"/>`;
    }
    return "";
  }

  function overProps() {
    return `
    <g class="c-p c-p-dots">
      <circle cx="115" cy="31" r="3" fill="#fff" stroke="#c9c2b8"/><circle cx="123" cy="21" r="4.5" fill="#fff" stroke="#c9c2b8"/>
      <ellipse cx="141" cy="7" rx="17" ry="11" fill="#fff" stroke="#c9c2b8"/>
      <circle class="c-dot d1" cx="133.5" cy="7" r="2.3" fill="#7a6f63"/><circle class="c-dot d2" cx="141" cy="7" r="2.3" fill="#7a6f63"/>
      <circle class="c-dot d3" cx="148.5" cy="7" r="2.3" fill="#7a6f63"/></g>
    <g class="c-p c-p-term">
      <rect x="112" y="-6" width="42" height="27" rx="4" fill="#1d2330" stroke="#3a4152"/>
      <text x="118" y="12" font-family="Consolas, 'Courier New', monospace" font-size="12" font-weight="700" fill="#7fe0a0">&gt;</text>
      <rect class="c-cursor" x="127" y="10" width="8" height="2.6" fill="#7fe0a0"/></g>
    <g class="c-fx c-fx-zzz" font-family="system-ui, sans-serif" font-weight="800" fill="#7a8bd6">
      <text class="z1" x="112" y="30" font-size="17">Z</text><text class="z2" x="125" y="15" font-size="13">z</text>
      <text class="z3" x="136" y="3" font-size="10">z</text></g>
    <path class="c-fx c-fx-sweat" d="M119 36 q-6 9 0 12.5 q6 -3.5 0 -12.5z" fill="#9ad3f5" stroke="#5aa7d6" stroke-width="1.2"/>
    <g class="c-fx c-fx-alert"><circle cx="134" cy="14" r="10.5" fill="#d64545" stroke="#fff" stroke-width="2"/>
      <rect x="132.4" y="6.5" width="3.2" height="9.5" rx="1.6" fill="#fff"/><circle cx="134" cy="20" r="1.9" fill="#fff"/></g>
    <g class="c-fx c-fx-shield"><path d="M80 102 L101 110 Q101 139 80 152 Q59 139 59 110 Z" fill="#d64545" stroke="#fff" stroke-width="3"/>
      <rect x="78.2" y="114" width="3.6" height="18" rx="1.8" fill="#fff"/><circle cx="80" cy="139" r="2.4" fill="#fff"/></g>`;
  }

  function headGroup(name, c) {
    return `<g class="c-head">
      ${hairBack(name, c)}
      <circle class="o" cx="41" cy="72" r="7" fill="${c.skin}"/><circle class="o" cx="119" cy="72" r="7" fill="${c.skin}"/>
      <circle class="o" cx="80" cy="66" r="40" fill="${c.skin}"/>
      ${face(name, c)}
      ${hairFront(name, c)}
      ${hat(name)}
    </g>`;
  }

  function build(name) {
    const c = LOOK[name] || LOOK._;
    return `<svg class="ch" viewBox="0 0 160 200" aria-hidden="true" focusable="false">
      <ellipse class="c-shadow" cx="80" cy="196" rx="36" ry="5.5"/>
      <g class="c-body">
        ${legs(c)}
        <g class="c-upper">
          ${back(name)}
          ${torso(c)}
          ${outfit(name, c)}
          ${heldProps()}
          ${arms(c)}
          ${headGroup(name, c)}
          ${overProps()}
        </g>
      </g>
    </svg>`;
  }

  function head(name) {
    const c = LOOK[name] || LOOK._;
    return `<svg class="mini" viewBox="28 8 104 104" aria-hidden="true" focusable="false">${headGroup(name, c)}</svg>`;
  }

  window.JevChars = { build, head, META };
})();
