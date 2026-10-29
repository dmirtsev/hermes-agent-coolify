"""Conservative repair triggers for the existing managed transit contract.

This does not replace Cabinet's authoritative native calculation validator.
It identifies deterministic publication failures while the same durable Hermes
operation can still repair them. No rejected draft becomes conversation history.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

if __package__:
    from .hermes_context_rows import ContextRowsError, unpack_context_rows
else:
    from hermes_context_rows import ContextRowsError, unpack_context_rows

VALIDATION_VERSION = "hermes-managed-transit-v1"


class ManagedValidationContextError(ValueError):
    pass


@dataclass(frozen=True)
class ManagedTransitContext:
    refs: frozenset[str]
    base_refs: frozenset[str]
    # ref -> (native participant/aspect identity, supplied exact UTC instants)
    intervals: dict[str, tuple[tuple[str, str, str], frozenset[str]]]
    synthesis: bool = False


def _rows(value):
    try:
        return unpack_context_rows(value)
    except ContextRowsError as error:
        raise ManagedValidationContextError(str(error)) from None


def prepare_managed_transit_context(body):
    """Prepare once before dispatch; only the existing standard period route."""
    if not isinstance(body, dict):
        return None
    mode = body.get("tp_dialogue_mode")
    if isinstance(mode, dict):
        mode = mode.get("code")
    method = body.get("tp_managed_method_context")
    calculation = method.get("calculation") if isinstance(method, dict) else None
    if (mode == "free_dialogue" or body.get("tp_answer_format") != "hermes_managed_v1"
            or body.get("tp_execution_mode") != "interpretation_facts_v1"
            or not isinstance(calculation, dict) or calculation.get("contextType") != "period_context_v1"):
        return None
    try:
        if body.get("stream") is True:
            raise ManagedValidationContextError("managed_transit_stream_not_supported")
        messages = body["messages"]
        roles = [m["role"] for m in messages]
        if roles[:2] != ["system", "user"] or len(roles) not in (2, 3) or any(role != "user" for role in roles[2:]):
            raise ManagedValidationContextError("managed_transit_messages_invalid")
        user = json.loads(messages[1]["content"])
        facts = user["facts"]
        period = user["context"]["transitPeriod"]
        native = _rows(calculation["facts"])
        if (native != _rows(period["facts"]) or not native
                or calculation["factRefs"] != [f.get("fact_id", f.get("id")) for f in native]
                or any(facts["source"][key] != calculation[key] or period[key] != calculation[key]
                       for key in ("calculationId", "resultId"))):
            raise ManagedValidationContextError("managed_transit_native_context_mismatch")
        statements = _rows(facts["statements"])
        refs = [f["ref"] for f in statements]
        if not all(isinstance(ref, str) and re.fullmatch(r"s\d+", ref) for ref in refs) or len(set(refs)) != len(refs):
            raise ManagedValidationContextError("managed_transit_reference_context_invalid")
        bases = [layer["layerId"] for layer in facts["layers"]
                 if layer.get("chartType") == "natal" and layer.get("relationMethod") == "base"]
        if len(bases) != 1:
            raise ManagedValidationContextError("managed_transit_base_ambiguous")
        base_refs = frozenset(f["ref"] for f in statements
                              if f.get("layerId") == bases[0] and f.get("status") == "present")
        transit = [f for f in statements if f.get("aspectScope") == "between_layers"
                   and f.get("fromLayerId") == "transit_period" and f.get("toLayerId") == bases[0]]
        if len(transit) != len(native):
            raise ManagedValidationContextError("managed_transit_reference_context_mismatch")
        intervals = {}
        for statement, band in zip(transit, native):
            if body.get("tp_context_encoding") == "period_lossless_v2" and statement.get("sourceRef") != band.get("fact_id", band.get("id")):
                raise ManagedValidationContextError("managed_transit_reference_context_mismatch")
            group = (band["transit_planet"]["code"], band["natal_point"]["code"], band["aspect"]["code"])
            if not all(isinstance(code, str) and code for code in group):
                raise ManagedValidationContextError("managed_transit_native_identity_invalid")
            exact = band["time_window"]["exact_times"]
            instants = frozenset(item["at_utc"] for item in exact)
            if not all(isinstance(instant, str) and instant for instant in instants):
                raise ManagedValidationContextError("managed_transit_native_times_invalid")
            intervals[statement["ref"]] = (group, instants)
        synthesis = isinstance(messages[0].get("content"), str) and "period_synthesis_v1" in messages[0]["content"]
        return ManagedTransitContext(frozenset(refs), base_refs, intervals, synthesis)
    except ManagedValidationContextError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        raise ManagedValidationContextError("managed_transit_context_invalid") from None


_MARKER = re.compile(r"\{\{(s\d+)\}\}")
_ANY_MARKER = re.compile(r"\{\{[^{}]*\}\}")
_NATAL = re.compile(r"\bнатальн[а-яё]*\b", re.I)
_TRANSIT = re.compile(r"\b(?:транзит|соляр|лунар|прогресси|дирекци)[а-яё]*\b", re.I)
_MOTION = re.compile(r"\b(?:ретроград|директ|стационар)[а-яё]*\b|\b(?:прям|обратн)[а-яё]*\s+движени[а-яё]*\b", re.I)
_DAYPART = re.compile(r"\b(?:утро[м]?|утренн[а-яё]*|вечер[а-яё]*|полден[а-яё]*|полдня|дн[её]м|ноч[а-яё]*)\b|"
                      r"\b(?:середин[а-яё]*|перв[а-яё]*\s+половин[а-яё]*|втор[а-яё]*\s+половин[а-яё]*)\s+дня\b", re.I)
_REPEAT = re.compile(r"\b(?:повторн[а-яё]*|повторяется|снова|вновь|дважды|трижды|двойн[а-яё]*|тройн[а-яё]*)\b", re.I)
_PASS = re.compile(r"\b(?:прохожд[а-яё]*|проход(?:ит|ят|ил|ила|ило|или))\b", re.I)
_BODY = re.compile(r"\b(?:солнц[а-яё]*|лун[а-яё]*|меркури[а-яё]*|венер[а-яё]*|марс[а-яё]*|юпитер[а-яё]*|"
                   r"сатурн[а-яё]*|уран[а-яё]*|нептун[а-яё]*|плутон[а-яё]*|лилит|уз[её]л|узл[а-яё]*|"
                   r"асцендент[а-яё]*|десцендент[а-яё]*)\b", re.I)
_ASPECT = re.compile(r"\b(?:соединени[а-яё]*|секстил[а-яё]*|квадрат[а-яё]*|тригон[а-яё]*|трин[а-яё]*|"
                     r"квинконс[а-яё]*|оппозици[а-яё]*)\b", re.I)
_PLACEMENT = re.compile(r"\bв\s+(?:\d+|[а-яё-]+)\s+(?:доме|знаке)\b", re.I)
_PRECISION = re.compile(r"\d{1,4}[./-]\d{1,2}|\d{1,2}:\d{2}|\d+(?:[.,]\d+)?\s*°|\bорбис[а-яё]*\b|"
                        r"\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-яё]*\b", re.I)
_START_TODAY = re.compile(r"\b(?:сегодня|с\s+этого\s+дня)[^.!?\n]{0,45}\b(?:начина[а-яё]*|заканчива[а-яё]*|заверша[а-яё]*)\b|"
                         r"\b(?:начина[а-яё]*|заканчива[а-яё]*|заверша[а-яё]*)[^.!?\n]{0,45}\bсегодня\b", re.I)
_THEORY = re.compile(r"^(?:если\b|в\s+общей\s+(?:теории|трактовке)|в\s+качестве\s+примера|гипотетическ)", re.I)


def managed_transit_issues(content, body_or_context):
    context = (body_or_context if isinstance(body_or_context, ManagedTransitContext)
               else prepare_managed_transit_context(body_or_context))
    if context is None:
        return []
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return ["managed_json_invalid"]
    if not isinstance(value, dict):
        return ["managed_json_invalid"]
    mode = value.get("mode")
    if mode == "facts":
        items = value.get("items")
        if not isinstance(items, list) or not items:
            return ["managed_structure_invalid"]
        return [] if all(isinstance(item, dict) and isinstance(item.get("refs"), list)
                         and bool(item["refs"]) and all(isinstance(ref, str) and ref in context.refs for ref in item["refs"])
                         for item in items) else ["managed_references_invalid"]
    paragraphs = value.get("paragraphs")
    if mode != "interpretation" or not isinstance(paragraphs, list) or not paragraphs:
        return ["managed_structure_invalid"]
    issues = set()
    for paragraph in paragraphs:
        if (not isinstance(paragraph, dict) or not isinstance(paragraph.get("text"), str)
                or not paragraph["text"].strip() or not isinstance(paragraph.get("refs"), list)
                or not all(isinstance(ref, str) for ref in paragraph["refs"])):
            issues.add("managed_structure_invalid")
            continue
        text, refs = paragraph["text"], set(paragraph["refs"])
        markers = set(_MARKER.findall(text))
        if (not refs <= context.refs or not markers <= refs
                or any(not _MARKER.fullmatch(marker) for marker in _ANY_MARKER.findall(text))):
            issues.add("managed_references_invalid")
        prose = _MARKER.sub(" ", text).strip()
        clean = re.sub(r"^[\s*#\d.)-]+", "", prose)
        eligible = markers & refs
        if _DAYPART.search(prose):
            issues.add("unprovided_day_segment")
        if _MOTION.search(prose):
            issues.add("unmarked_native_motion")
        if _PRECISION.search(prose):
            issues.add("unmarked_native_precision")
        if _START_TODAY.search(prose):
            issues.add("unmarked_interval_boundary")
        if _REPEAT.search(prose) and _PASS.search(prose):
            groups = {}
            for ref in eligible & context.intervals.keys():
                group, instants = context.intervals[ref]
                groups.setdefault(group, set()).update(instants)
            if not groups or not all(len(instants) >= 2 for instants in groups.values()):
                issues.add("unprovided_repeat_pass")
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", prose):
            theory = bool(_THEORY.search(re.sub(r"^[\s*#\d.)-]+", "", sentence)))
            calculation = _BODY.search(sentence) and (_ASPECT.search(sentence) or _PLACEMENT.search(sentence))
            if calculation and not theory and not (_NATAL.search(sentence) and not _TRANSIT.search(sentence)
                                    and eligible & context.base_refs):
                issues.add("unmarked_native_calculation")
    return sorted(issues)


def managed_transit_repair_prompt(issues, context=None):
    codes = ", ".join(sorted(set(issues)))
    if context is not None and context.synthesis:
        refs = sorted(context.intervals, key=lambda value: int(value[1:]))[:1]
        if context.base_refs:
            refs = [min(context.base_refs, key=lambda value: int(value[1:]))] + refs
        skeleton = {"mode": "interpretation", "presentation": "period_synthesis_v1", "paragraphs": [
            {"text": "Общая тема связывает внешние задачи с привычными способами принимать решения. "
                     "Сверяйте важные договорённости и оставляйте запас на уточнения.", "refs": refs}]}
        return (
            "[System repair " + VALIDATION_VERSION + ": The previous draft was not published. "
            "Validation issues: " + codes + ". Return one fresh complete JSON object for the SAME "
            "original question, full supplied native facts and full published conversation. "
            "Preserve the requested period synthesis and depth; do not turn it into a sequence of "
            "mini references for individual transits. Keep mode=interpretation and "
            "presentation=period_synthesis_v1. Main text explains qualitative meaning, interactions, "
            "contradictions and development of relevant themes against the natal basis. Each paragraph "
            "uses only its relevant existing current refs; Cabinet supplies their exact native facts "
            "in the calculation basis. Do not add a marker at the beginning of every paragraph. "
            "Do not write dates, times, orb values, concrete aspect names, placements or directions "
            "in free prose. Do not invent morning, midday, evening or halves of a day. Do not invent "
            "events, merge intervals across gaps or treat repeated passes as independent evidence. "
            "Do not shorten the requested analysis to a summary or copy the rejected draft. "
            "Пиши связный подробный разбор по-русски, от общей картины к существенным темам "
            "и применению; не создавай технический список и заголовок расчётной основы. "
            "Пример только формата: " + json.dumps(skeleton, ensure_ascii=False) + ". JSON only.]"
        )
    example = ""
    if context is not None and context.intervals:
        ref = min(context.intervals, key=lambda value: int(value[1:]))
        skeleton = {"mode": "interpretation", "paragraphs": [
            {"text": "{{"+ref+"}}. Сверяйте важные договорённости и оставляйте запас на уточнения.",
             "refs": [ref]}]}
        example = (
            " Исправь ответ по-русски, без заголовка и без даты в свободном тексте. "
            "Каждый абзац с расчётным основанием начинай существующим маркером и указывай "
            "тот же ref в refs; затем пиши только качественную трактовку и практический совет. "
            "Не пиши вне маркеров названия планет, аспектов, направления движения и части суток. "
            "Не употребляй в свободном тексте слова «повторное», «прохождение» и их формы. "
            "Пример только формата (основания выбери по исходному вопросу, объём сохрани): "
            + json.dumps(skeleton, ensure_ascii=False) + "."
        )
    return (
        "[System repair " + VALIDATION_VERSION + ": The previous draft was not published. "
        "Validation issues: " + codes + ". Return one fresh complete JSON object for the SAME "
        "original question, full supplied native facts and full published conversation. "
        "Use each calculated statement ONLY through {{sN}} with the SAME sN in that paragraph's refs. "
        "Do not repeat aspect names, placements, directions, dates, times or orb values in free prose. "
        "Use 'this factor/interval' and give qualitative interpretation beside the native markers. "
        "Do not invent morning, midday, evening or halves of a day. An overlapping interval does not "
        "begin today. Do not call a pass repeated unless the cited native markers supply at least "
        "two exact instants for that SAME participant/aspect group. A single supplied exact time "
        "is ONE pass. Do not copy or continue the rejected draft. Preserve requested depth; "
        "the user can request further coherent parts." + example + " JSON only.]"
    )
