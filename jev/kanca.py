"""Claude PreToolUse kancası başlatıcısı.

Kanca komutu bu dosyayı yol ile çalıştırır: `"<python>" "<...>/jev/kanca.py" --kosu ... --proje ...`.
Böylece Jev kurulu olmasa ya da ajanın ortamına PYTHONPATH eklenmese de `jev.guard` bulunur.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev.guard import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
