"""Soru-cevap yardımcıları (iki komutun ortak kullandığı)."""


def ask(question: str, default: str) -> str:
    return input(f"{question} [{default}]: ").strip() or default


def choose(title: str, options: list[str], default: str, multi: bool = False) -> list[int]:
    """Numaralı liste gösterir, seçilen indeksleri döndürür. multi: '1 3' ya da 'hepsi'."""
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        answer = ask("Seçim" + (" (ör. 1 3 ya da hepsi)" if multi else ""), default)
        if multi and answer == "hepsi":
            return list(range(len(options)))
        try:
            idx = [int(x) - 1 for x in answer.replace(",", " ").split()]
        except ValueError:
            idx = []
        if idx and all(0 <= i < len(options) for i in idx) and (multi or len(idx) == 1):
            return idx
        print("  Geçersiz seçim, tekrar dene.")
