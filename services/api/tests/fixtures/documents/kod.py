"""Örnek modül: bütçe hesapları."""


def hesapla(kira: int, maas: int, yazilim: int) -> int:
    """Üç kalemin toplamı."""
    return kira + maas + yazilim


def kdv(tutar: int, oran: float = 0.2) -> float:
    return tutar * oran


class Butce:
    def __init__(self, kalemler: dict[str, int]) -> None:
        self.kalemler = kalemler

    def toplam(self) -> int:
        return sum(self.kalemler.values())
