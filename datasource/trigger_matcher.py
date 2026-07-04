"""Avaliação de padrões de trigger: LIKE (%), AND, OR e IN(a,b,c)."""
from __future__ import annotations

import re
from typing import Optional

_REGEX_ESCAPE = re.compile(r"([.^$+?{}[\]|\\()])")


class PatternSyntaxError(ValueError):
    pass


def matches_pattern(message: str, expression: str, case_sensitive: bool = False) -> bool:
    """Retorna True se a mensagem satisfaz a expressão de padrão."""
    expr = (expression or "").strip()
    if not expr:
        return False
    try:
        return _eval_or(message or "", expr, case_sensitive)
    except PatternSyntaxError:
        raise
    except Exception as e:
        raise PatternSyntaxError(f"Erro ao interpretar padrão: {e}") from e


def validate_pattern(expression: str) -> None:
    """Valida sintaxe do padrão (levanta PatternSyntaxError se inválido)."""
    expr = (expression or "").strip()
    if not expr:
        raise PatternSyntaxError("Padrão não pode ser vazio")
    matches_pattern("", expr, False)


def _eval_or(message: str, expression: str, case_sensitive: bool) -> bool:
    parts = _split_top_level(expression, "or")
    if len(parts) > 1:
        return any(_eval_and(message, part, case_sensitive) for part in parts)
    return _eval_and(message, expression, case_sensitive)


def _eval_and(message: str, expression: str, case_sensitive: bool) -> bool:
    parts = _split_top_level(expression, "and")
    if len(parts) > 1:
        return all(_eval_factor(message, part, case_sensitive) for part in parts)
    return _eval_factor(message, expression, case_sensitive)


def _eval_factor(message: str, expression: str, case_sensitive: bool) -> bool:
    expr = expression.strip()
    if not expr:
        raise PatternSyntaxError("Expressão vazia entre operadores")

    if _has_outer_parens(expr):
        return _eval_or(message, expr[1:-1].strip(), case_sensitive)

    in_match = re.match(r"^in\s*\((.*)\)\s*$", expr, re.IGNORECASE | re.DOTALL)
    if in_match:
        args = _split_in_args(in_match.group(1))
        if not args:
            raise PatternSyntaxError("IN() não pode ser vazio")
        return any(_match_like_term(message, arg, case_sensitive) for arg in args)

    return _match_like_term(message, expr, case_sensitive)


def _split_top_level(expression: str, operator: str) -> list[str]:
    op = operator.lower()
    op_len = len(op)
    parts: list[str] = []
    start = 0
    depth = 0
    i = 0
    text = expression
    lower = text.lower()

    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            if depth == 0:
                raise PatternSyntaxError("Parêntese ')' sem correspondente")
            depth -= 1
            i += 1
            continue

        if depth == 0 and lower[i : i + op_len] == op:
            before = text[i - 1] if i > 0 else " "
            after = text[i + op_len] if i + op_len < len(text) else " "
            if before.isspace() and after.isspace():
                part = text[start:i].strip()
                if not part:
                    raise PatternSyntaxError(f"Operador '{operator}' sem operando")
                parts.append(part)
                i += op_len
                start = i
                while start < len(text) and text[start].isspace():
                    start += 1
                i = start
                continue
        i += 1

    if depth != 0:
        raise PatternSyntaxError("Parêntese '(' sem fechamento")

    tail = text[start:].strip()
    if not tail and parts:
        raise PatternSyntaxError(f"Operador '{operator}' sem operando")
    if tail:
        parts.append(tail)
    return parts if parts else [expression.strip()]


def _split_in_args(raw: str) -> list[str]:
    args: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in raw:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            if depth == 0:
                raise PatternSyntaxError("Parêntese ')' inválido dentro de IN()")
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if depth != 0:
        raise PatternSyntaxError("Parêntese '(' sem fechamento dentro de IN()")
    args.append("".join(buf).strip())
    return [a for a in args if a]


def _has_outer_parens(expression: str) -> bool:
    if not (expression.startswith("(") and expression.endswith(")")):
        return False
    depth = 0
    for i, ch in enumerate(expression):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and i < len(expression) - 1:
            return False
    return depth == 0


def _match_like_term(message: str, term: str, case_sensitive: bool) -> bool:
    pattern = term.strip()
    if not pattern:
        raise PatternSyntaxError("Termo vazio no padrão")
    if "%" not in pattern:
        pattern = f"%{pattern}%"
    regex = _like_to_regex(pattern)
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.search(regex, message, flags) is not None


def _like_to_regex(like_pattern: str) -> str:
    if like_pattern == "%%":
        return "^[\\s\\S]*$"
    out: list[str] = ["^"]
    i = 0
    while i < len(like_pattern):
        ch = like_pattern[i]
        if ch == "%":
            out.append(".*")
            i += 1
        elif ch == "_":
            out.append(".")
            i += 1
        else:
            out.append(_REGEX_ESCAPE.sub(r"\\\1", ch))
            i += 1
    out.append("$")
    return "".join(out)
