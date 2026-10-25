"""Belirsiz kelime işaretleme: ana model ile tesseract'ın farklı okuduğu kelimeler sarı işaret alır."""
import re
from difflib import SequenceMatcher

from ocrnow.layout import Mark

# tesseract'ın sistematik karışmaları; t/r gibi gerçek hatalar kasten eşlenmez
_CONFUSIONS = str.maketrans({"û": "ü", "î": "i", "â": "a", "ı": "i"})
_DROPPED = str.maketrans("", "", "'’‘ʿʾ`´-")
_LINE_END_JOIN = re.compile(r"(?<=\w)[-¬.,]\s*\n\s*(?=\w)")
_LETTER_DIGIT = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")


def _keys(token: str) -> list[str]:
    """Bir kelimenin karşılaştırma anahtarları; harf+rakam bitişikliği ayrılır, baştaki/sondaki noktalama atılır."""
    keys = []
    for part in _LETTER_DIGIT.sub(" ", token).split():
        part = part.replace("İ", "i").replace("I", "ı").lower()
        start, end = 0, len(part)
        while start < end and not part[start].isalnum():
            start += 1
        while end > start and not part[end - 1].isalnum():
            end -= 1
        key = part[start:end].translate(_CONFUSIONS).translate(_DROPPED)
        if key:
            keys.append(key)
    return keys


def flag_uncertain(text: str, tess_text: str) -> list[Mark]:
    """`text` içinde tesseract'tan farklı okunan kelimelerin karakter aralıkları (başlangıç dahil, bitiş hariç)."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    main_keys: list[str] = []
    owner: list[int] = []  # anahtar -> kelime sırası
    for i, (s, e) in enumerate(spans):
        for key in _keys(text[s:e]):
            main_keys.append(key)
            owner.append(i)
    tess_keys = [k for tok in _LINE_END_JOIN.sub("", tess_text).split() for k in _keys(tok)]

    bad: set[int] = set()
    for tag, i1, i2, _, _ in SequenceMatcher(None, main_keys, tess_keys, autojunk=False).get_opcodes():
        if tag != "equal":
            bad.update(owner[i1:i2])
    return [Mark(spans[i][0], spans[i][1], "belirsiz") for i in sorted(bad)]
