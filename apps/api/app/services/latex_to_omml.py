from __future__ import annotations

import re
from collections.abc import Callable

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

_LATEX_SYMBOLS = {
    r"\to": "→",
    r"\leftarrow": "←",
    r"\rightarrow": "→",
    r"\leftrightarrow": "↔",
    r"\Leftarrow": "⇐",
    r"\Rightarrow": "⇒",
    r"\Leftrightarrow": "⇔",
    r"\Longleftarrow": "⟸",
    r"\Longrightarrow": "⟹",
    r"\Longleftrightarrow": "⟺",
    r"\infty": "∞",
    r"\cdot": "·",
    r"\times": "×",
    r"\div": "÷",
    r"\le": "≤",
    r"\ge": "≥",
    r"\leq": "≤",
    r"\geq": "≥",
    r"\approx": "≈",
    r"\ne": "≠",
    r"\neq": "≠",
    r"\neg": "¬",
    r"\pm": "±",
    r"\sim": "∼",
    r"\forall": "∀",
    r"\exists": "∃",
    r"\in": "∈",
    r"\notin": "∉",
    r"\mid": "|",
    r"\subset": "⊂",
    r"\subseteq": "⊆",
    r"\supset": "⊃",
    r"\supseteq": "⊇",
    r"\cup": "∪",
    r"\cap": "∩",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\varepsilon": "ε",
    r"\zeta": "ζ",
    r"\eta": "η",
    r"\theta": "θ",
    r"\iota": "ι",
    r"\kappa": "κ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\xi": "ξ",
    r"\pi": "π",
    r"\rho": "ρ",
    r"\varrho": "ρ",
    r"\sigma": "σ",
    r"\tau": "τ",
    r"\upsilon": "υ",
    r"\phi": "φ",
    r"\varphi": "φ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    r"\Gamma": "Γ",
    r"\Delta": "Δ",
    r"\Theta": "Θ",
    r"\Lambda": "Λ",
    r"\Xi": "Ξ",
    r"\Pi": "Π",
    r"\Sigma": "Σ",
    r"\Phi": "Φ",
    r"\Psi": "Ψ",
    r"\Omega": "Ω",
    r"\partial": "∂",
    r"\int": "∫",
    r"\sum": "∑",
    r"\prod": "∏",
    r"\dots": "…",
    r"\cdots": "⋯",
    r"\ldots": "…",
}
_LATEX_TEXT_COMMANDS = {r"\text", r"\mathrm", r"\operatorname", r"\ce", r"\pu"}
_LATEX_STYLE_COMMANDS = {r"\displaystyle", r"\textstyle", r"\scriptstyle", r"\scriptscriptstyle"}
_LATEX_DELIMITER_COMMANDS = {r"\left", r"\right"}
_LATEX_SPACING_COMMANDS = {r"\quad", r"\qquad", r"\,", r"\;", r"\:", r"\!"}


def _m_element(tag: str, text: str | None = None) -> OxmlElement:
    element = OxmlElement(f"m:{tag}")
    if text is not None:
        element.text = text
    return element


def _math_run(text: str) -> OxmlElement:
    run = _m_element("r")
    text_node = _m_element("t", text)
    run.append(text_node)
    return run


def _math_container(tag: str, children: list[OxmlElement]) -> OxmlElement:
    container = _m_element(tag)
    for child in children:
        container.append(child)
    return container


def _math_arg(tag: str, children: list[OxmlElement]) -> OxmlElement:
    return _math_container(tag, children or [_math_run("")])


def _math_property_value(tag: str, attr: str, value: str) -> OxmlElement:
    element = _m_element(tag)
    element.set(qn(f"m:{attr}"), value)
    return element


def _math_fraction(numerator: list[OxmlElement], denominator: list[OxmlElement]) -> OxmlElement:
    fraction = _m_element("f")
    fraction_properties = _m_element("fPr")
    fraction_properties.append(_math_property_value("type", "val", "bar"))
    fraction.append(fraction_properties)
    fraction.append(_math_arg("num", numerator))
    fraction.append(_math_arg("den", denominator))
    return fraction


def _math_script(
    base: list[OxmlElement],
    subscript: list[OxmlElement] | None,
    superscript: list[OxmlElement] | None,
) -> OxmlElement:
    if subscript is not None and superscript is not None:
        node = _m_element("sSubSup")
        node.append(_m_element("sSubSupPr"))
        node.append(_math_arg("e", base))
        node.append(_math_arg("sub", subscript))
        node.append(_math_arg("sup", superscript))
        return node
    if subscript is not None:
        node = _m_element("sSub")
        node.append(_m_element("sSubPr"))
        node.append(_math_arg("e", base))
        node.append(_math_arg("sub", subscript))
        return node
    node = _m_element("sSup")
    node.append(_m_element("sSupPr"))
    node.append(_math_arg("e", base))
    node.append(_math_arg("sup", superscript or [_math_run("")]))
    return node


def _math_limit_low(base: list[OxmlElement], limit: list[OxmlElement]) -> OxmlElement:
    node = _m_element("limLow")
    node.append(_m_element("limLowPr"))
    node.append(_math_arg("e", base))
    node.append(_math_arg("lim", limit))
    return node


def _plain_latex_text_argument(value: str) -> str:
    text = value
    for command in sorted(_LATEX_SPACING_COMMANDS, key=len, reverse=True):
        text = text.replace(command, " " if command != r"\!" else "")
    for command, symbol in _LATEX_SYMBOLS.items():
        text = text.replace(command, symbol)
    return re.sub(r"\s+", " ", text).strip()


def _matching_delimiter(value: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    for index in range(start, len(value)):
        if value[index] == opener:
            depth += 1
        elif value[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _read_braced(value: str, index: int) -> tuple[str, int]:
    while index < len(value) and value[index].isspace():
        index += 1
    if index >= len(value):
        return "", index
    if value[index] != "{":
        return value[index], index + 1
    end = _matching_delimiter(value, index, "{", "}")
    if end < 0:
        return value[index + 1 :], len(value)
    return value[index + 1 : end], end + 1


def _read_parenthesized(value: str, index: int) -> tuple[str, int]:
    end = _matching_delimiter(value, index, "(", ")")
    if end < 0:
        return value[index], index + 1
    return value[index : end + 1], end + 1


def _read_base(value: str, index: int) -> tuple[list[OxmlElement], int]:
    if index >= len(value):
        return [_math_run("")], index
    if value[index] == "(":
        parenthesized, next_index = _read_parenthesized(value, index)
        inner = parenthesized[1:-1] if parenthesized.startswith("(") and parenthesized.endswith(")") else ""
        if inner:
            return [_math_run("("), *latex_to_math_children(inner), _math_run(")")], next_index
        return [_math_run(parenthesized)], next_index
    if value[index] == "{":
        braced, next_index = _read_braced(value, index)
        return latex_to_math_children(braced), next_index
    if value[index] == "\\":
        command_match = re.match(r"\\[A-Za-z]+", value[index:])
        if command_match:
            command = command_match.group(0)
            if command in _LATEX_TEXT_COMMANDS:
                raw, next_index = _read_braced(value, index + len(command))
                return [_math_run(_plain_latex_text_argument(raw))], next_index
            if command in _LATEX_STYLE_COMMANDS or command in _LATEX_DELIMITER_COMMANDS:
                return [_math_run("")], index + len(command)
            return [_math_run(_LATEX_SYMBOLS.get(command, command[1:]))], index + len(command)
    match = re.match(r"[A-Za-z]+(?:')?", value[index:])
    if match:
        token = match.group(0)
        return [_math_run(token)], index + len(token)
    return [_math_run(value[index])], index + 1


def _read_script(value: str, index: int) -> tuple[list[OxmlElement], int]:
    script, next_index = _read_braced(value, index)
    return latex_to_math_children(script), next_index


def _apply_scripts(value: str, index: int, base: list[OxmlElement]) -> tuple[list[OxmlElement], int]:
    subscript: list[OxmlElement] | None = None
    superscript: list[OxmlElement] | None = None
    current = index
    while current < len(value) and value[current] in {"_", "^"}:
        script_kind = value[current]
        script, current = _read_script(value, current + 1)
        if script_kind == "_":
            subscript = script
        else:
            superscript = script
    if subscript is None and superscript is None:
        return base, index
    return [_math_script(base, subscript, superscript)], current


def latex_to_math_children(latex: str) -> list[OxmlElement]:
    children: list[OxmlElement] = []
    index = 0
    while index < len(latex):
        char = latex[index]
        matched_style = next((command for command in _LATEX_STYLE_COMMANDS if latex.startswith(command, index)), "")
        if matched_style:
            index += len(matched_style)
            continue
        matched_delimiter = next((command for command in _LATEX_DELIMITER_COMMANDS if latex.startswith(command, index)), "")
        if matched_delimiter:
            index += len(matched_delimiter)
            continue
        matched_text = next((command for command in _LATEX_TEXT_COMMANDS if latex.startswith(command, index)), "")
        if matched_text:
            raw_text, index = _read_braced(latex, index + len(matched_text))
            if raw_text:
                children.append(_math_run(_plain_latex_text_argument(raw_text)))
            continue
        if latex.startswith(r"\begin{cases}", index):
            end_index = latex.find(r"\end{cases}", index)
            if end_index >= 0:
                raw_cases = latex[index + len(r"\begin{cases}") : end_index]
                children.extend(_latex_cases_children(raw_cases))
                index = end_index + len(r"\end{cases}")
                continue
        if char.isspace():
            if not children or (children[-1].tag != qn("m:r") or (children[-1].find(qn("m:t")).text or "") != " "):
                children.append(_math_run(" "))
            index += 1
            continue
        if char == "{":
            braced, index = _read_braced(latex, index)
            children.extend(latex_to_math_children(braced))
            continue
        if latex.startswith(r"\frac", index):
            numerator_raw, after_num = _read_braced(latex, index + len(r"\frac"))
            denominator_raw, after_den = _read_braced(latex, after_num)
            fraction = _math_fraction(latex_to_math_children(numerator_raw), latex_to_math_children(denominator_raw))
            scripted, index = _apply_scripts(latex, after_den, [fraction])
            children.extend(scripted)
            continue
        if latex.startswith(r"\lim", index):
            next_index = index + len(r"\lim")
            if next_index < len(latex) and latex[next_index] == "_":
                limit_raw, next_index = _read_braced(latex, next_index + 1)
                lim_node = _math_limit_low([_math_run("lim")], latex_to_math_children(limit_raw))
                scripted, index = _apply_scripts(latex, next_index, [lim_node])
                children.extend(scripted)
                continue
            children.append(_math_run("lim"))
            index = next_index
            continue
        if char == "\\":
            matched_spacing = next((command for command in _LATEX_SPACING_COMMANDS if latex.startswith(command, index)), "")
            if matched_spacing:
                if matched_spacing != r"\!":
                    children.append(_math_run("    " if matched_spacing in {r"\quad", r"\qquad"} else " "))
                index += len(matched_spacing)
                continue
            if latex.startswith(r"\|", index):
                children.append(_math_run("‖"))
                index += len(r"\|")
                continue
            command_match = re.match(r"\\[A-Za-z]+", latex[index:])
            if command_match:
                command = command_match.group(0)
                text = _LATEX_SYMBOLS.get(command, command[1:])
                node, next_index = _apply_scripts(latex, index + len(command), [_math_run(text)])
                children.extend(node)
                index = next_index
                continue
        base, next_index = _read_base(latex, index)
        scripted, index = _apply_scripts(latex, next_index, base)
        children.extend(scripted)
    return children or [_math_run(latex)]


def _latex_cases_children(raw_cases: str) -> list[OxmlElement]:
    children: list[OxmlElement] = [_math_run("{ ")]
    normalized_cases = re.sub(r"\\\\\[[^\]]+\]", r"\\\\", raw_cases)
    rows = [row.strip() for row in re.split(r"\\\\", normalized_cases) if row.strip()]
    for index, row in enumerate(rows):
        if index:
            children.append(_math_run("; "))
        row_latex = " ".join(part.strip() for part in row.split("&") if part.strip())
        children.extend(latex_to_math_children(row_latex))
    children.append(_math_run(" }"))
    return children


def append_omml_math(
    paragraph,
    latex: str,
    *,
    display: bool = False,
    normalize_latex: Callable[[str], str] | None = None,
) -> None:
    normalized_latex = normalize_latex(latex) if normalize_latex else latex
    math = _m_element("oMath")
    for child in latex_to_math_children(normalized_latex):
        math.append(child)
    if display:
        math_paragraph = _m_element("oMathPara")
        math_paragraph.append(math)
        paragraph._p.append(math_paragraph)
    else:
        paragraph._p.append(math)
