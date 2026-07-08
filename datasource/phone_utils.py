"""Variantes de telefone BR para casar DDI e 9º dígito celular."""
from __future__ import annotations

import re


def phone_digit_variants(phone: str) -> set[str]:
    """
    Gera variantes numéricas equivalentes (com/sem 55 e com/sem o 9 após o DDD).
    Ex.: 41998500111 <-> 4198500111 <-> 5541998500111 <-> 554198500111
    """
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return set()

    variants: set[str] = {digits}
    if digits.startswith("55"):
        variants.add(digits[2:])
        full = digits
    else:
        full = "55" + digits
        variants.add(full)

    if not full.startswith("55") or len(full) < 12:
        return {v for v in variants if v}

    ddd = full[2:4]
    rest = full[4:]
    if len(full) == 13 and rest.startswith("9"):
        alt = "55" + ddd + rest[1:]
        variants.add(alt)
        variants.add(alt[2:])
    elif len(full) == 12:
        alt = "55" + ddd + "9" + rest
        variants.add(alt)
        variants.add(alt[2:])

    return {v for v in variants if v}
