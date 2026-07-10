"""Parser simples de comandos curl para requisições HTTP."""
from __future__ import annotations

import re
import shlex
from typing import Any
from urllib.parse import parse_qsl

PHONE_VARIABLE = "{telefone}"
PHONE_VARIABLE_RE = re.compile(r"\{telefone\}", re.IGNORECASE)


class CurlParseError(ValueError):
    pass


def parse_curl(command: str) -> dict[str, Any]:
    """
    Converte um comando curl em dict com method, url, headers e data.
    Variável de telefone do chat: {telefone}
    """
    text = _normalize_curl_text(command)
    if not text.lower().startswith("curl"):
        raise CurlParseError("Comando deve começar com curl")

    try:
        tokens = shlex.split(text)
    except ValueError as e:
        raise CurlParseError(f"Comando curl inválido: {e}") from e

    if not tokens:
        raise CurlParseError("Comando curl vazio")

    method = "GET"
    url = ""
    headers: dict[str, str] = {}
    data_items: list[tuple[str, str]] = []
    data_raw: str | None = None

    i = 1
    while i < len(tokens):
        token = tokens[i]
        lower = token.lower()

        if lower in ("-x", "--request"):
            if i + 1 >= len(tokens):
                raise CurlParseError("Método HTTP ausente após -X/--request")
            method = tokens[i + 1].upper()
            i += 2
            continue

        if lower in ("-h", "--header"):
            if i + 1 >= len(tokens):
                raise CurlParseError("Header ausente após -H/--header")
            header = tokens[i + 1]
            if ":" in header:
                name, value = header.split(":", 1)
                headers[name.strip()] = value.strip()
            i += 2
            continue

        if lower in ("-d", "--data", "--data-raw", "--data-urlencode"):
            if i + 1 >= len(tokens):
                raise CurlParseError("Corpo ausente após flag de dados")
            payload = tokens[i + 1]
            if lower == "--data-urlencode":
                data_items.extend(parse_qsl(payload, keep_blank_values=True))
            elif "=" in payload and "&" in payload:
                data_items.extend(parse_qsl(payload, keep_blank_values=True))
            elif "=" in payload and not payload.strip().startswith("{"):
                key, value = payload.split("=", 1)
                data_items.append((key.strip(), value.strip()))
            else:
                data_raw = payload
            i += 2
            continue

        if token.startswith("http://") or token.startswith("https://"):
            url = token
            i += 1
            continue

        if token == "--location":
            i += 1
            continue

        i += 1

    if not url:
        raise CurlParseError("URL não encontrada no comando curl")

    if method == "GET" and (data_items or data_raw is not None):
        method = "POST"

    phone_param = _detect_phone_param(data_items, url, data_raw)
    return {
        "method": method or "GET",
        "url": url,
        "headers": headers,
        "data": dict(data_items),
        "data_raw": data_raw,
        "phone_param": phone_param,
    }


def has_phone_variable(text: str) -> bool:
    return PHONE_VARIABLE_RE.search(text or "") is not None


def is_phone_variable_value(value: str) -> bool:
    return bool(PHONE_VARIABLE_RE.fullmatch((value or "").strip()))


def substitute_phone(request: dict[str, Any], phone: str) -> dict[str, Any]:
    """Substitui {telefone} pelo número do chat."""
    out = dict(request)
    out["headers"] = {
        key: substitute_phone_in_text(value, phone)
        for key, value in (request.get("headers") or {}).items()
    }
    out["data"] = dict(request.get("data") or {})
    out["url"] = substitute_phone_in_text(str(request.get("url") or ""), phone)

    param = str(request.get("phone_param") or "").strip()
    digits = _normalize_digits(phone)
    if param and digits and param in out["data"]:
        out["data"][param] = _format_phone_for_api(digits)

    data_raw = request.get("data_raw")
    if data_raw is not None:
        out["data_raw"] = substitute_phone_in_text(str(data_raw), phone)

    for key, value in list(out["data"].items()):
        if isinstance(value, str) and PHONE_VARIABLE_RE.search(value):
            out["data"][key] = substitute_phone_in_text(value, phone)

    return out


def substitute_phone_in_text(text: str, phone: str) -> str:
    if not text or not PHONE_VARIABLE_RE.search(text):
        return text
    digits = _normalize_digits(phone)
    if not digits:
        return text
    return PHONE_VARIABLE_RE.sub(lambda _m: _format_phone_for_api(digits), text)


def validate_phone_variable(curl_text: str) -> None:
    """Exige {telefone} no curl."""
    if not has_phone_variable(curl_text):
        raise CurlParseError(
            f"Use a variável {PHONE_VARIABLE} no curl, "
            f"ex.: --data-urlencode 'celular={PHONE_VARIABLE}'"
        )
    request = parse_curl(curl_text)
    if not request.get("phone_param"):
        raise CurlParseError(
            f"Não foi possível identificar onde substituir {PHONE_VARIABLE}. "
            f"Use ex.: --data-urlencode 'celular={PHONE_VARIABLE}'"
        )


def _format_phone_for_api(digits: str) -> str:
    """Envia DDD+número (11 dígitos) quando possível; API costuma esperar sem DDI 55."""
    if digits.startswith("55") and len(digits) >= 12:
        return digits[2:]
    if len(digits) > 11:
        return digits[-11:]
    return digits


def _normalize_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    if len(digits) >= 10 and not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _normalize_curl_text(command: str) -> str:
    lines = []
    for line in (command or "").splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            lines.append(stripped[:-1].strip() + " ")
        else:
            lines.append(stripped)
    return " ".join(part.strip() for part in lines if part.strip())


def _detect_phone_param(
    data_items: list[tuple[str, str]],
    url: str = "",
    data_raw: str | None = None,
) -> str:
    for key, value in data_items:
        if is_phone_variable_value(value) or PHONE_VARIABLE_RE.search(value or ""):
            return key

    if PHONE_VARIABLE_RE.search(url or ""):
        return "url"

    if data_raw and PHONE_VARIABLE_RE.search(data_raw):
        return "body"

    return ""
