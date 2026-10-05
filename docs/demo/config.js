window.JevDemoConfig = {
  "labels": {
    "status": {
      "pending": "Bekliyor",
      "ready": "Sırada",
      "running": "Çalışıyor",
      "verifying": "Doğrulanıyor",
      "done": "Bitti",
      "needs_decision": "Sorunlu",
      "waiting_quota": "Kota bekliyor",
      "split": "Bölündü",
      "skipped": "Atlandı",
      "failed": "Başarısız",
      "blocked": "Engellendi"
    },
    "phase": {
      "sizing": "Ölçekleme",
      "planning": "Plan",
      "awaiting_approval": "Onay bekliyor",
      "decomposing": "Plan",
      "executing": "Uygulama",
      "reviewing": "Son kontrol",
      "reported": "Rapor",
      "fixing": "Düzeltme",
      "paused": "Duraklatıldı",
      "aborted": "İptal edildi"
    },
    "verdict": {
      "basarili": "Başarılı",
      "kismen": "Kısmen başarılı",
      "basarisiz": "Başarısız"
    },
    "criteria": {
      "met": "karşılandı",
      "partial": "kısmen",
      "unmet": "karşılanmadı",
      "unverified": "doğrulanamadı"
    },
    "outcome": {
      "done": "başarılı",
      "verify_failed": "doğrulama başarısız",
      "agent_failed": "ajan başarısız dedi",
      "agent_blocked": "ajan ilerleyemedi (blocked)",
      "timeout": "zaman aşımı",
      "crash": "süreç hatası",
      "guard": "koruma durdurdu",
      "schema": "geçersiz çıktı",
      "quota": "kota doldu",
      "cancelled": "durduruldu",
      "auth": "oturum kapalı",
      "no_diff": "değişiklik yok",
      "merge_conflict": "birleştirme çakışması",
      "missing_outputs": "beklenen dosyalar yok",
      "audit": "denetim sorunu",
      "transient": "geçici hata",
      "not_accepted": "Jev kabul etmedi",
      "refusal": "güvenlik reddi"
    },
    "decision": {
      "retry": "tekrar dene",
      "reassign": "başka ajana ver",
      "split": "böl",
      "revise": "görevi düzelt",
      "skip": "atla",
      "pause": "duraklat",
      "abort": "iptal et"
    },
    "display": {
      "opus": "Opus",
      "sol": "Sol",
      "sonnet": "Sonnet",
      "luna": "Luna",
      "jev": "Jev"
    },
    "role": {
      "planlayici": "Planlayıcı",
      "denetci": "Denetçi",
      "beyin": "Beyin",
      "eskalasyon": "Eskalasyon",
      "isci": "İşçi",
      "orkestrator": "Orkestratör"
    },
    "call_phase": {
      "plan": "Plan ve kartlar",
      "worker": "Görev",
      "brain": "Jev'e karar",
      "repair": "Şema onarımı",
      "review": "Son kontrol",
      "fix": "Düzeltme kartları",
      "test": "Deneme",
      "decompose": "Görevlere bölme",
      "netlestir": "Netleştirme"
    },
    "deviation": {
      "none": "",
      "minor": "plandan küçük sapma",
      "major": "plandan büyük sapma"
    },
    "applied": {
      "retry": "yeniden denenecek",
      "reassign": "başka ajana verildi",
      "revise": "görev düzeltildi",
      "split": "bölündü",
      "skipped": "atlandı",
      "pause": "koşu duraklatıldı",
      "abort": "koşu iptal edildi",
      "failed": "başarısız sayıldı",
      "gecersiz": "geçersiz karar; kural uygulandı"
    },
    "type": {
      "code": "Kod",
      "test": "Test",
      "docs": "Belge",
      "config": "Yapılandırma",
      "research": "Araştırma"
    }
  },
  "agents": [
    {
      "name": "opus",
      "display": "Opus",
      "label": "Claude Opus 5.5",
      "model": "claude-opus-5-5",
      "provider": "claude",
      "roles": [
        "planlayici",
        "beyin",
        "isci",
        "eskalasyon"
      ],
      "roles_tr": [
        "Planlayıcı",
        "Beyin",
        "İşçi",
        "Eskalasyon"
      ]
    },
    {
      "name": "sol",
      "display": "Sol",
      "label": "GPT-6.1-Sol",
      "model": "gpt-6.1-sol",
      "provider": "codex",
      "roles": [
        "isci",
        "denetci"
      ],
      "roles_tr": [
        "İşçi",
        "Denetçi"
      ]
    },
    {
      "name": "sonnet",
      "display": "Sonnet",
      "label": "Claude Sonnet 5.5",
      "model": "claude-sonnet-5-5",
      "provider": "claude",
      "roles": [
        "isci"
      ],
      "roles_tr": [
        "İşçi"
      ]
    },
    {
      "name": "luna",
      "display": "Luna",
      "label": "GPT-6-Luna",
      "model": "gpt-6-luna",
      "provider": "codex",
      "roles": [
        "isci"
      ],
      "roles_tr": [
        "İşçi"
      ]
    },
    {
      "name": "jev",
      "display": "Jev",
      "label": "Orkestratör",
      "model": "jev-latest",
      "provider": "typesafe",
      "roles": [
        "orkestrator"
      ],
      "roles_tr": [
        "Orkestratör"
      ],
      "brain": "Opus",
      "brain_display": "Opus",
      "fallback": "Sol"
    }
  ],
  "repository_url": "https://github.com/nowackk-cp/jev"
};
