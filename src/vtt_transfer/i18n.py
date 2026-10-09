"""Localisation of everything that ends up in the HTML report.

The transfer code never stores finished sentences: notes, checks, actions and group
reasons are `Msg` objects (a catalog key plus parameters), rendered in the report
language chosen by the operator. `str(msg)` gives English, which is what the console uses.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

LANGS = ("en", "ru")
DEFAULT_LANG = "ru"


@dataclass(frozen=True)
class Msg:
    key: str
    params: dict = field(default_factory=dict)

    def __str__(self):
        return tr(self, "en")


def M(key: str, **params) -> Msg:
    if key not in MESSAGES:
        raise KeyError(f"unknown message key: {key}")
    return Msg(key, params)


def _param(v, lang):
    if isinstance(v, Msg):
        return tr(v, lang)
    if isinstance(v, list):
        return "; ".join(str(_param(x, lang)) for x in v)
    return v


def tr(x, lang: str = "en") -> str:
    """Render a Msg in `lang`; anything else (raw data, compiler output) is returned as text."""
    if not isinstance(x, Msg):
        return str(x)
    en, ru = MESSAGES[x.key]
    template = ru if lang == "ru" else en
    return template.format(**{k: _param(v, lang) for k, v in x.params.items()})


class Translator:
    """Report-side helper bound to one language."""

    def __init__(self, lang: str):
        if lang not in LANGS:
            raise ValueError(f"unsupported report language: {lang}")
        self.lang = lang

    def __call__(self, x, **params) -> str:
        """Plain text: a Msg, a catalog key with parameters, or raw data."""
        if isinstance(x, str) and x in MESSAGES:
            x = Msg(x, params)
        return tr(x, self.lang)

    def h(self, key: str, **params) -> str:
        """HTML: the template is trusted markup, parameters are escaped."""
        esc = {k: html.escape(str(_param(v, self.lang))) for k, v in params.items()}
        return tr(Msg(key, esc), self.lang)


# key: (English, Russian)
MESSAGES: dict[str, tuple[str, str]] = {
    # ------------------------------------------------------------ checks (core)
    "chk_upm_same": ("UPM matches", "UPM совпадает"),
    "chk_axes_same": ("Axes match", "Оси совпадают"),
    "chk_axes_differ": ("Axes differ", "Оси отличаются"),
    "chk_axes_differ_detail": (
        "before: {old}; now: {new}. Glyph hints are transferred, CVT variations (cvar/TSIC) are not.",
        "было: {old}; стало: {new}. Хинты глифов переносятся, но вариации CVT (cvar/TSIC) — нет."),
    "axes_none": ("none", "нет"),
    "chk_avar_differ": ("avar differs", "avar отличается"),
    "chk_avar_differ_detail": (
        "Normalized coordinates have shifted: the instances the hints were checked on are now at "
        "different points of the design space.",
        "Нормализованные координаты сместились: инстансы, на которых проверяли хинты, "
        "теперь в других точках пространства."),
    "chk_regions_same": ("gvar regions (masters) match", "Регионы gvar (мастера) совпадают"),
    "chk_regions_count": ("{n} regions", "{n} регионов"),
    "chk_regions_differ": ("The set of masters changed", "Набор мастеров изменился"),
    "chk_regions_differ_detail": (
        "gvar regions: {old} before, {new} now; added: {added}, gone: {gone}. "
        "New regions have no CVT variations — check them in VTT.",
        "было {old} регионов gvar, стало {new}; новых: {added}, исчезло: {gone}. "
        "Для новых регионов нет вариаций CVT — проверьте их в VTT."),
    "chk_target_hinted": ("The target font already had hints/VTT sources",
                          "В целевом шрифте уже были хинты/исходники VTT"),
    "chk_target_hinted_detail": ("tables {tables} replaced with data from the source font",
                                 "таблицы {tables} заменены данными из исходного шрифта"),
    "chk_gasp_replaced": ("gasp replaced with the source one", "gasp заменён на исходный"),
    "chk_gasp_replaced_detail": ("was {old}, now {new}", "был {old}, стал {new}"),
    "chk_cvar_ok": ("CVT variations (cvar/TSIC) transferred", "Вариации CVT (cvar/TSIC) перенесены"),
    "chk_cvar_skipped": ("CVT variations not transferred", "Вариации CVT не перенесены"),
    "chk_cvar_skipped_detail": ("axes or avar changed — set per-master CVT again in VTT",
                                "оси или avar изменились — задайте CVT по мастерам заново в VTT"),
    "chk_cvar_none": ("The source has no cvar/TSIC", "В исходнике нет cvar/TSIC"),
    "chk_cvar_none_detail": (
        "CVT values are the same for all instances. If stems or heights in the extreme masters differ "
        "noticeably from the default, consider setting CVT variations in VTT.",
        "CVT одинаковые для всех инстансов. Если в крайних мастерах штрихи или высоты "
        "заметно отличаются от default, стоит задать вариации CVT в VTT."),
    "chk_volt": ("VOLT tables were not transferred", "Таблицы VOLT не переносились"),
    "chk_metric_removed": ("{tag} removed", "{tag} удалён"),
    "chk_metric_removed_detail": ("the hinted font's metrics cache would be stale",
                                  "кэш метрик хинтованного шрифта устарел бы"),
    "chk_no_vttcompilepy": ("vttcompilepy is not installed", "vttcompilepy не установлен"),
    "chk_no_vttcompilepy_detail": ("compilation skipped — compile in VTT",
                                   "компиляция пропущена — скомпилируйте в VTT"),
    "chk_compile_failed": ("Compilation failed", "Компиляция не прошла"),
    "chk_compile_ok": ("vttcompilepy compilation succeeded", "Компиляция vttcompilepy прошла"),
    "chk_compile_all": ("all glyphs", "все глифы"),
    "chk_compile_partial": ("after removing hints from {n} glyphs", "после снятия хинтов с {n} глифов"),
    "chk_outlines_differ": ("Outlines or composites in the result differ from the target font",
                            "Контуры или композиты в результате отличаются от целевого шрифта"),
    "chk_outlines_differ_detail": ("{items}{more}", "{items}{more}"),
    "bad_type": ("{n}: glyph type", "{n}: тип глифа"),
    "bad_components": ("{n}: components {a} ≠ {b}", "{n}: компоненты {a} ≠ {b}"),
    "bad_outline": ("{n}: outline", "{n}: контур"),
    "chk_outlines_ok": ("Outlines and composites match the target font",
                        "Контуры и композиты совпадают с целевым шрифтом"),
    "n_glyphs": ("{n} glyphs", "{n} глифов"),
    "chk_tables_changed": ("Tables unrelated to hinting changed", "Изменились таблицы, не связанные с хинтингом"),
    "chk_tables_ok": ("Other tables (gvar, GSUB, GPOS…) are unchanged",
                      "Остальные таблицы (gvar, GSUB, GPOS…) не изменены"),
    "chk_empty_bytecode": ("Some transferred glyphs have empty bytecode after compilation",
                           "У части перенесённых глифов пустой байткод после компиляции"),

    # ------------------------------------------------------------ glyph notes (core)
    "note_umm": ("USE_MY_METRICS of component {c} taken from the new font",
                 "USE_MY_METRICS у компонента {c} взят из нового шрифта"),
    "note_anchor": ("component {c} is attached by points (ANCHOR) — check manually",
                    "компонент {c} привязан по точкам (ANCHOR) — проверьте вручную"),
    "note_soffset": ("component {c} has a transformation (SOFFSET) — check it",
                     "компонент {c} с трансформацией (SOFFSET) — проверьте"),
    "note_twin_shifted": ("outline matches {twin} shifted by {dx} along X: only Y hints copied",
                          "контур совпадает с {twin}, но сдвинут по X на {dx}: скопированы только Y-хинты"),
    "note_twin_exact": ("hints copied from {twin}: outline matches point for point",
                        "хинты скопированы с {twin}: контур совпадает точка в точку"),
    "note_width": ("advance width differs: {old} → {new}", "ширина отличается: {old} → {new}"),
    "note_x_removed": ("X commands removed: {n}", "удалено X-команд: {n}"),
    "note_renamed": ("renamed: {old} → {new}", "переименован: {old} → {new}"),
    "note_type": ("type changed: {old} → {new}", "тип изменился: {old} → {new}"),
    "kind_simple": ("simple", "простой"),
    "kind_composite": ("composite", "композит"),
    "kind_empty": ("empty", "пустой"),
    "note_asm_only": ("hints existed only in assembly (no VTT Talk) — automatic remap is impossible",
                      "хинты были только в ассемблере (нет VTT Talk) — автоматический ремап невозможен"),
    "note_unsupported": ("commands that cannot be remapped automatically: {cmds}",
                         "команды, которые не ремапятся автоматически: {cmds}"),
    "note_no_remap": ("remap disabled (--no-remap)", "ремап отключён (--no-remap)"),
    "note_lost": ("reference points not found in the new outline: {lost} of {total}",
                  "опорные точки не найдены в новом контуре: {lost} из {total}"),
    "note_points": ("points: {a} → {b}, contours: {c} → {d}", "точек: {a} → {b}, контуров: {c} → {d}"),
    "note_components": ("components: {pairs}", "компоненты: {pairs}"),
    "note_offsets": ("component offsets changed (taken from the new font)",
                     "смещения компонентов изменились (взяты из нового шрифта)"),
    "note_ncomponents": ("component count: {a} → {b}", "число компонентов: {a} → {b}"),
    "note_comp_dropped": ("composite's own instructions removed: the point structure of its components changed",
                          "собственные инструкции композита сняты: изменилась структура точек компонентов"),
    "note_comp_dropped_in": (
        "composite's own instructions removed: the point structure of its components changed ({names})",
        "собственные инструкции композита сняты: изменилась структура точек компонентов ({names})"),
    "note_compile_error": ("compile error, hints removed: {msg}", "ошибка компиляции, хинты сняты: {msg}"),

    # ------------------------------------------------------------ lost points (core)
    "lost_range": ("point number out of range", "номер точки вне диапазона"),
    "lost_ambiguous": ("several points at {p}", "несколько точек с координатами {p}"),
    "lost_missing": ("point {p} is gone, nearest is {j} {q}", "точки {p} больше нет, ближайшая — {j} {q}"),
    "lost_collision": ("two points mapped to the same one", "две точки сопоставились с одной"),

    # ------------------------------------------------------------ character groups (core)
    "grp_composites": ("as most composites in the source", "как у большинства композитов в исходнике"),
    "grp_empty": ("empty glyph", "пустой глиф"),
    "grp_base": ("same as base glyph {base}", "как у базового глифа {base}"),
    "grp_marks": ("same as other combining marks in the new version",
                  "как у других комбинируемых знаков в новой версии"),
    "grp_role": ("same as “{role}” glyphs in the source", "как у глифов категории «{role}» в исходнике"),
    "grp_spacing": ("combining mark — same as spacing accents in the source",
                    "комбинируемый знак — как у спейсинговых акцентов в исходнике"),
    "grp_unknown": ("category unknown", "категория не определена"),
    "role_Lu": ("uppercase", "прописные"),
    "role_Ll": ("lowercase", "строчные"),
    "role_Nd": ("figures", "цифры"),
    "role_No": ("other figures", "прочие цифры"),
    "role_sup": ("superiors/inferiors", "индексы"),
    "role_paren": ("brackets", "скобки"),
    "role_Mn": ("combining marks", "комбинируемые знаки"),
    "role_Sk": ("modifier symbols", "модификаторы"),
    "role_Lm": ("modifier letters", "буквы-модификаторы"),
    "role_Lo": ("other letters", "прочие буквы"),
    "role_Po": ("punctuation", "пунктуация"),
    "role_Pd": ("dashes", "тире"),
    "role_Sm": ("math symbols", "мат. знаки"),
    "role_Sc": ("currency", "валюты"),
    "role_So": ("symbols", "символы"),

    # ------------------------------------------------------------ technical actions (core)
    "act_copied": ("{tag}: copied from the source font", "{tag}: скопирован из исходного шрифта"),
    "act_copied_short": ("{tag}: copied", "{tag}: скопирован"),
    "act_maxp": ("maxp (interpreter limits) and head flags 3/4: from the source font",
                 "maxp (лимиты интерпретатора) и флаги head 3/4: из исходного шрифта"),
    "act_tsi": ("TSI0–TSI3, TSI5: rebuilt by the glyph names of the new font",
                "TSI0–TSI3, TSI5: собраны заново по именам глифов нового шрифта"),

    # ------------------------------------------------------------ report: header and summary
    "r_title": ("VTT hint transfer", "Перенос VTT-хинтов"),
    "r_h1": ("VTT hint transfer: {stem}", "Перенос VTT-хинтов: {stem}"),
    "r_source": ("Source: <b>{p}</b>", "Исходник: <b>{p}</b>"),
    "r_target": ("New version: <b>{p}</b>", "Новая версия: <b>{p}</b>"),
    "r_output": ("Result: <b>{p}</b>", "Результат: <b>{p}</b>"),
    "r_hprj": ("Autohinter project: <b>{out}</b> (from {src})", "Проект автохинтера: <b>{out}</b> (из {src})"),
    "r_compiled": ("compiled with vttcompilepy", "скомпилировано vttcompilepy"),
    "r_not_compiled": ("not compiled", "без компиляции"),
    "st_transferred": ("hinted glyphs transferred (of {total})", "хинтованных глифов перенесено (из {total})"),
    "st_remapped": ("transferred with point remap", "перенесено с ремапом точек"),
    "st_rehint": ("to re-hint", "перехинтовать"),
    "st_dropped": ("composites: restore own instructions", "композитов: восстановить свои инструкции"),
    "st_twins": ("new glyphs got hints from a twin", "новых глифов получили хинты двойника"),
    "st_new_simple": ("new simple glyphs without hints", "новых простых глифов без хинтов"),
    "st_new_comp": ("new composites ({n} waiting for components)", "новых композитов ({n} ждут компоненты)"),
    "st_removed": ("glyphs removed", "глифов удалено"),

    # ------------------------------------------------------------ report: what to do
    "sec_todo": ("What to do", "Что сделать"),
    "sec_todo_intro": ("The operator's work order.", "Порядок работы оператора."),
    "todo_open": ("Open <b>{stem}</b> in VTT, run <i>Compile everything</i> and make sure there are no errors.",
                  "Открыть <b>{stem}</b> в VTT, выполнить <i>Compile everything</i> и убедиться, что ошибок нет."),
    "todo_errors": ("<b>There are verification errors ({n})</b> — see “Checks” below before you start.",
                    "<b>Есть ошибки проверки ({n})</b> — см. раздел «Проверки» ниже, до начала работы."),
    "todo_rehint": ("<a href='#rehint'>Re-hint {n} glyphs</a> whose point structure changed: their hints were "
                    "removed, the old VTT Talk is attached to each card.",
                    "<a href='#rehint'>Перехинтовать {n} глифов</a>, у которых изменилась структура "
                    "точек: хинты с них сняты, старый VTT Talk приложен к каждой карточке."),
    "todo_twins": ("<a href='#twins'>Look over {n} new glyphs</a> whose hints were copied from an identical "
                   "or X-shifted old glyph.",
                   "<a href='#twins'>Глянуть {n} новых глифов</a>, которым хинты скопированы с "
                   "идентичного или сдвинутого по X старого глифа."),
    "todo_dropped": ("<a href='#dropped'>Restore the instructions of {n} composites</a> (usually diacritic "
                     "positioning): the components are hinted, only the composite's own commands were removed.",
                     "<a href='#dropped'>Восстановить инструкции у {n} композитов</a> (обычно позиционирование "
                     "диакритики): компоненты хинтованы, сняты только команды самого композита."),
    "todo_new": ("<a href='#new'>Hint {n} new simple glyphs</a>. Character groups were assigned "
                 "automatically — check them.",
                 "<a href='#new'>Отхинтовать {n} новых простых глифов</a>. "
                 "Character groups назначены автоматически — проверьте."),
    "todo_newcomp_wait": ("<a href='#newcomp'>{n} new composites</a> will become hinted once their components "
                          "are hinted.",
                          "<a href='#newcomp'>{n} новых композитов</a> станут хинтованными, когда будут "
                          "отхинтованы их компоненты."),
    "todo_newcomp_ready": ("Review {n} new composites built from already hinted components — usually no extra "
                           "hinting is needed, but check diacritic placement at small sizes.",
                           "Просмотреть {n} новых композитов из уже хинтованных компонентов — "
                           "обычно дохинтовка не нужна, но стоит проверить положение диакритики на мелких кеглях."),
    "todo_hprj": ("<a href='#hprj'>Check the updated {name}</a>: {n} new glyphs added to autohinter classes.",
                  "<a href='#hprj'>Проверить обновлённый {name}</a>: {n} новых глифов "
                  "добавлены в классы автохинтера."),
    "todo_remapped": ("<a href='#remapped'>Check {n} glyphs with remapped points</a>.",
                      "<a href='#remapped'>Проверить {n} глифов с ремапом точек</a>."),
    "todo_geom": ("<a href='#geom'>Skim {n} glyphs</a>: same structure, hints transferred, but the outline moved.",
                  "<a href='#geom'>Пробежать глазами {n} глифов</a>: структура та же, хинты перенесены, "
                  "но контур сдвинут."),

    # ------------------------------------------------------------ report: checks
    "sec_checks": ("Checks", "Проверки"),
    "sec_checks_intro": ("Font compatibility and verification of the compiled result.",
                         "Совместимость шрифтов и проверка результата после компиляции."),
    "th_check": ("Check", "Проверка"),
    "th_details": ("Details", "Детали"),
    "lvl_ok": ("OK", "OK"),
    "lvl_info": ("Info", "Инфо"),
    "lvl_warn": ("Warning", "Внимание"),
    "lvl_error": ("Error", "Ошибка"),

    # ------------------------------------------------------------ report: glyph cards
    "card_group": (" · group {g}", " · группа {g}"),
    "card_old_asm": ("Old composite instructions", "Старые инструкции композита"),
    "card_old_talk": ("Old VTT Talk", "Старый VTT Talk"),
    "card_used_in": ("Used in {n} composites: {names}", "Используется в {n} композитах: {names}"),
    "card_waiting": ("waiting for: {names}", "ждёт: {names}"),
    "card_all_hinted": ("all components are hinted", "все компоненты хинтованы"),
    "card_points": ("points: {map}", "точки: {map}"),
    "card_points_same": ("numbers unchanged", "номера не изменились"),
    "copy_names": ("Names as a list", "Имена списком"),
    "legend": ("<i></i>old outline (dashed), filled — new; red dots — reference points of the old hints "
               "that are missing from the new outline.",
               "<i></i>старый контур (пунктир), заливка — новый; "
               "красные точки — опорные точки старых хинтов, которых нет в новом контуре."),

    # ------------------------------------------------------------ report: sections
    "sec_rehint": ("Re-hint", "Перехинтовать"),
    "sec_rehint_intro": ("Glyphs that were hinted, but whose point structure changed so that the reference point "
                         "numbers cannot be restored. Their hints were removed.",
                         "Глифы, которые были хинтованы, но их точечная структура "
                         "изменилась так, что номера опорных точек восстановить нельзя. Хинты сняты."),
    "sec_dropped": ("Composites: restore their own instructions", "Композиты: восстановить собственные инструкции"),
    "sec_dropped_intro": ("One of the components changed its number of points, so the composite's commands "
                          "(SHC, CALL etc. after OFFSET) would refer to the wrong points and contours. "
                          "They were removed; the assembly and the component hints are in place. "
                          "The old code is attached.",
                          "У одного из компонентов изменилось число точек, поэтому команды композита "
                          "(SHC, CALL и т. п. после OFFSET) ссылались бы не на те точки и контуры. "
                          "Они сняты; сборка и хинты компонентов на месте. Старый код приложен."),
    "sec_twins": ("New glyphs with hints from a twin", "Новые глифы с хинтами двойника"),
    "sec_twins_intro": ("The points match a hinted glyph of the old version — exactly (a renamed copy) or "
                        "shifted along X (e.g. a combining accent made from a spacing one). In the latter case "
                        "only Y hints were transferred.",
                        "Точки совпадают с хинтованным глифом "
                        "старой версии — точно (переименованная копия) или со сдвигом по X (например, "
                        "комбинируемый акцент из спейсингового). Во втором случае перенесены только Y-хинты."),
    "sec_new": ("New simple glyphs", "Новые простые глифы"),
    "sec_new_intro": ("No hints; within the section, glyphs used in more composites come first. The character "
                      "group is assigned by analogy with glyphs of the same category in the source.",
                      "Хинтов нет; внутри раздела сначала те, что используются в большем числе композитов. "
                      "Группа (character group) назначена по аналогии "
                      "с глифами той же категории в исходнике."),
    "sec_newcomp": ("New composites", "Новые композиты"),
    "sec_newcomp_intro": ("The assembly code (OFFSET) was generated from the new font in the source's style "
                          "({style}). Components carry their own hints, so a composite is hinted when all its "
                          "components are.",
                          "Код сборки (OFFSET) сгенерирован по новому шрифту в стиле "
                          "исходника ({style}). Компоненты несут свои хинты, поэтому композит "
                          "хинтован, если хинтованы все его компоненты."),
    "newcomp_wait": ("Waiting for unhinted components · {n}", "Ждут нехинтованные компоненты · {n}"),
    "newcomp_deps": ("Components they depend on ({n}):", "Компоненты, от которых они зависят ({n}):"),
    "newcomp_ready": ("Built from hinted components · {n}", "Из хинтованных компонентов · {n}"),
    "sec_remapped": ("Transferred with point remap", "Перенесено с ремапом точек"),
    "sec_remapped_intro": ("The outline changed, but all reference points were found in the new outline by exact "
                           "coordinates; the numbers in VTT Talk were rewritten and the assembly was rebuilt by "
                           "the compiler. Check visually.",
                           "Контур изменился, но все опорные точки "
                           "нашлись в новом контуре по точным координатам; номера в VTT Talk переписаны, "
                           "ассемблер пересобран компилятором. Проверьте визуально."),
    "sec_typechanged": ("Became composites", "Стали композитами"),
    "sec_typechanged_intro": ("Used to be simple glyphs with their own hints, now built from components.",
                              "Раньше были простыми глифами со своими хинтами, "
                              "теперь собраны из компонентов."),
    "sec_relinked": ("Composites with changed assembly", "Композиты с изменённой сборкой"),
    "sec_relinked_intro": ("Hints transferred, the assembly code (OFFSET) updated from the new font.",
                           "Хинты перенесены, код сборки (OFFSET) обновлён по новому шрифту."),
    "sec_geom": ("Outline moved, same structure", "Контур сдвинут при той же структуре"),
    "sec_geom_intro": ("Hints transferred as is. Worth a look at small sizes.",
                       "Хинты перенесены как есть. Стоит посмотреть на мелких кеглях."),
    "sec_changedunhinted": ("Structure changed, there were no hints before either",
                            "Структура изменилась, хинтов не было и раньше"),
    "sec_changedunhinted_intro": ("Nothing was lost.", "Ничего не потеряно."),
    "sec_names": ("Renames and removals", "Переименования и удаления"),
    "sec_names_intro": ("Glyphs are matched by name; renames are detected by Unicode and matching structure.",
                        "Глифы сопоставляются по именам; переименования "
                        "определены по Unicode и совпадению структуры."),
    "names_renamed": ("Renames", "Переименования"),
    "names_removed": ("Removed in the new version", "Удалены из новой версии"),
    "names_was_hinted": ("{n} (was hinted)", "{n} (был хинтован)"),
    "sec_unhinted": ("All unhinted simple glyphs of the result", "Все нехинтованные простые глифы результата"),
    "sec_unhinted_intro": ("Including those that were not hinted in the source either.",
                           "Включая те, что не были хинтованы и в исходнике."),
    "sec_groups": ("Character groups", "Character groups"),
    "sec_groups_intro": ("Group numbers from the source's TSI5 and example glyphs. The last column shows how many "
                         "glyphs were assigned the group automatically.",
                         "Номера групп из TSI5 исходника и примеры глифов. "
                         "Последняя колонка — сколько глифов получили группу автоматически."),
    "th_group": ("Group", "Группа"),
    "th_examples": ("Examples in the source", "Примеры в исходнике"),
    "th_assigned": ("Assigned", "Назначено"),
    "sec_hprj": ("Autohinter project (.hprj)", "Проект автохинтера (.hprj)"),
    "sec_hprj_intro": ("Classes are matched to TSI5 groups by the majority of their glyphs. New simple glyphs "
                       "were added to the class of their group; group 0 (Other) glyphs are not in any class.",
                       "Классы сопоставлены с группами TSI5 по большинству глифов класса. Новые простые глифы "
                       "добавлены в класс своей группы; глифы группы 0 (Other) в классы не входят."),
    "th_class": ("Class", "Класс"),
    "th_tsi5_group": ("TSI5 group", "Группа TSI5"),
    "th_glyphs": ("Glyphs", "Глифов"),
    "th_changes": ("Changes", "Изменения"),
    "hprj_added": ("<b>added ({n}):</b> {names}", "<b>добавлены ({n}):</b> {names}"),
    "hprj_renamed": ("<b>renamed:</b> {pairs}", "<b>переименованы:</b> {pairs}"),
    "hprj_removed": ("<b>removed (not in the new version):</b> {names}",
                     "<b>удалены (нет в новой версии):</b> {names}"),
    "hprj_now_composite": ("<b>now composites, kept in the list:</b> {names}",
                           "<b>теперь композиты, оставлены в списке:</b> {names}"),
    "hprj_unchanged": ("unchanged", "без изменений"),
    "hprj_unassigned": ("New glyphs whose group has no matching class (not added):",
                        "Новые глифы, чьей группе не соответствует ни один класс (не добавлены):"),
    "hprj_glyph_group": ("{n} (gr. {g})", "{n} (гр. {g})"),
    "sec_tech": ("Technical details", "Технические детали"),
    "sec_tech_intro": ("What exactly was done to the font.", "Что именно было сделано со шрифтом."),
    "tech_compiler": ("Compiler messages", "Сообщения компилятора"),

    # ------------------------------------------------------------ report: scripts
    "script_none": ("No Unicode (alternates, technical)", "Без Unicode (альтернаты, служебные)"),
    "script_vietnamese": ("Vietnamese", "Вьетнамский"),
    "script_latin": ("Latin", "Латиница"),
    "script_greek": ("Greek", "Греческий"),
    "script_cyrillic": ("Cyrillic", "Кириллица"),
    "script_combining": ("Combining marks", "Комбинируемые знаки"),
    "script_digits": ("Digits", "Цифры"),
    "script_other": ("Other", "Прочее"),
}
