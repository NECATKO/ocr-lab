"""Belirsiz kelime işaretleme: ana model ile tesseract'ın farklı okuduğu kelimeler sarı işaret alır."""
import re
from difflib import SequenceMatcher

from ocrnow.layout import Mark

# tesseract'ın sistematik karışmaları; t/r gibi gerçek hatalar kasten eşlenmez
_CONFUSIONS = str.maketrans({"û": "ü", "î": "i", "â": "a", "ı": "i"})
_DROPPED = str.maketrans("", "", "'’‘ʿʾ`´-")
_HYPHEN_JOIN = re.compile(r"(?<=\w)[-¬]\s*\n\s*(?=\w)")  # tire/¬ ile biten satır: koşulsuz birleşir
_PUNCT_JOIN = re.compile(r"(\S*\w)[.,]\s*\n\s*(\w\S*)")  # nokta/virgül: yalnız birleşik anahtar ana modelde varsa
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


def _main_keys(text: str, spans: list[tuple[int, int]]) -> tuple[list[str], list[tuple[int, ...]]]:
    """Ana model anahtarları ve her anahtarın ait olduğu kelime sıraları.
    `-` ile biten kelime + küçük harfle başlayan sonraki kelime tek anahtar olarak birleştirilir."""
    keys: list[str] = []
    owner: list[tuple[int, ...]] = []
    carry: tuple[str, int] | None = None  # önceki kelimenin `-` ile biten son anahtarı
    for i, (s, e) in enumerate(spans):
        token = text[s:e]
        ks = _keys(token)
        if carry and ks and token[0].isalpha() and token[0].islower():
            keys.append(carry[0] + ks[0])
            owner.append((carry[1], i))
            ks = ks[1:]
        elif carry:
            keys.append(carry[0])
            owner.append((carry[1],))
        carry = None
        if token.endswith("-") and ks:
            carry = (ks[-1], i)
            ks = ks[:-1]
        for key in ks:
            keys.append(key)
            owner.append((i,))
    if carry:
        keys.append(carry[0])
        owner.append((carry[1],))
    return keys, owner


def flag_uncertain(text: str, tess_text: str) -> list[Mark]:
    """`text` içinde tesseract'tan farklı okunan kelimelerin karakter aralıkları (başlangıç dahil, bitiş hariç).
    Tesseract'ta hiç kelime yoksa kanıt yoktur: boş liste döner."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    main_keys, owner = _main_keys(text, spans)
    known = set(main_keys)

    def join_punct(m: re.Match) -> str:
        joined = _keys(m.group(1) + m.group(2))
        return m.group(1) + m.group(2) if joined and all(k in known for k in joined) else m.group(0)

    tess_text = _PUNCT_JOIN.sub(join_punct, _HYPHEN_JOIN.sub("", tess_text))
    tess_keys = [k for tok in tess_text.split() for k in _keys(tok)]
    if not tess_keys:
        return []

    bad: set[int] = set()
    for tag, i1, i2, _, _ in SequenceMatcher(None, main_keys, tess_keys, autojunk=False).get_opcodes():
        if tag != "equal":
            for words in owner[i1:i2]:
                bad.update(words)
    return [Mark(spans[i][0], spans[i][1], "belirsiz") for i in sorted(bad)]
