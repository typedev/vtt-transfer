"""Self-contained HTML report for a VTT transfer run."""

from __future__ import annotations

import datetime
import html
import unicodedata

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen

from . import __version__
from . import core as C

E = html.escape


# ---------------------------------------------------------------- glyph drawing

class Drawer:
    def __init__(self, font):
        self.font = font
        self.gs = font.getGlyphSet()
        self.upm = font["head"].unitsPerEm
        hhea = font["hhea"]
        self.asc, self.desc = max(hhea.ascent, self.upm * 0.75), min(hhea.descent, -self.upm * 0.25)

    def path(self, name):
        if name not in self.gs:
            return "", None, 0
        pen = SVGPathPen(self.gs)
        self.gs[name].draw(pen)
        bp = BoundsPen(self.gs)
        self.gs[name].draw(bp)
        return pen.getCommands(), bp.bounds, self.gs[name].width


def glyph_svg(new: Drawer, name, old: Drawer | None = None, old_name=None, marks=None, size=130):
    d, bounds, adv = new.path(name)
    od, obounds, oadv = (old.path(old_name or name) if old else ("", None, 0))
    xs = [0, adv, oadv]
    for b in (bounds, obounds):
        if b:
            xs += [b[0], b[2]]
    x0, x1 = min(xs), max(xs + [new.upm * 0.3])
    pad = new.upm * 0.06
    asc, desc = new.asc, new.desc
    vb = f"{x0 - pad:.0f} {-asc - pad:.0f} {x1 - x0 + 2 * pad:.0f} {asc - desc + 2 * pad:.0f}"
    parts = [f'<svg class="g" viewBox="{vb}" height="{size}" xmlns="http://www.w3.org/2000/svg">',
             f'<line class="base" x1="{x0 - pad:.0f}" x2="{x1 + pad:.0f}" y1="0" y2="0"/>',
             f'<line class="base" x1="0" x2="0" y1="{-asc:.0f}" y2="{-desc:.0f}"/>',
             f'<line class="base" x1="{adv}" x2="{adv}" y1="{-asc:.0f}" y2="{-desc:.0f}"/>']
    if d:
        parts.append(f'<path class="new" transform="scale(1,-1)" d="{d}"/>')
    if od:
        parts.append(f'<path class="old" transform="scale(1,-1)" d="{od}"/>')
    r, fs = new.upm * 0.03, new.upm * 0.11
    for m in marks or []:
        if not m.get("xy"):
            continue
        x, y = m["xy"]
        parts.append(f'<circle class="lost" cx="{x}" cy="{-y}" r="{r:.0f}"/>'
                     f'<text class="lbl" x="{x + r * 1.5:.0f}" y="{-y - r:.0f}" font-size="{fs:.0f}">{m["index"]}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- small helpers

_REV = {}


def _cps(font, name):
    key = id(font)
    if key not in _REV:
        rev = {}
        for cp, n in sorted(font.getBestCmap().items()):
            rev.setdefault(n, []).append(cp)
        _REV[key] = rev
    return _REV[key].get(name, [])

def ulabel(res, name):
    cps = _cps(res.out_font, name)
    if not cps:
        return ""
    cp = cps[0]
    return f"U+{cp:04X} {unicodedata.name(chr(cp), '').lower()}"


def script_of(res, name):
    cps = _cps(res.out_font, name)
    cp = cps[0] if cps else None
    if cp is None:
        info = C.GlyphInfo(name, "", unicodes=[])
        cp = C.codepoint_of(info)
    if cp is None:
        return "Без Unicode (альтернаты, служебные)"
    n = unicodedata.name(chr(cp), "")
    if 0x1EA0 <= cp <= 0x1EF9 or (n.startswith("LATIN") and any(w in n for w in ("HORN", "HOOK ABOVE", "DOT BELOW"))):
        return "Вьетнамский"
    return {"LATIN": "Латиница", "GREEK": "Греческий", "CYRILLIC": "Кириллица",
            "COMBINING": "Комбинируемые знаки", "DIGIT": "Цифры"}.get(n.split(" ")[0] if n else "", "Прочее")


def chips(names, cls=""):
    if not names:
        return '<p class="muted">—</p>'
    return '<div class="chips">' + "".join(f'<span class="chip {cls}">{E(n)}</span>' for n in names) + "</div>"


def copybox(names, label="Имена списком"):
    if not names:
        return ""
    return (f'<details class="copy"><summary>{E(label)} ({len(names)})</summary>'
            f'<textarea readonly rows="3">{E(" ".join(names))}</textarea></details>')


def used_in_html(r):
    if not r.used_in:
        return ""
    names = ", ".join(r.used_in[:12]) + (" …" if len(r.used_in) > 12 else "")
    return f'<p class="used">Используется в {len(r.used_in)} композитах: {E(names)}</p>'


def card(res, r, new_d, old_d=None, show_old=False, extra=""):
    marks = r.lost_points if show_old else None
    svg = glyph_svg(new_d, r.name, old_d if show_old else None, r.source, marks, size=170 if show_old else 130)
    notes = "".join(f"<li>{E(n)}</li>" for n in r.notes)
    group = ""
    if r.group is not None:
        group = f" · группа {r.group}"
    talk = ""
    if r.old_asm:
        talk = f'<details><summary>Старые инструкции композита</summary><pre>{E(r.old_asm)}</pre></details>'
    elif r.old_talk and C.vtalk.has_code(r.old_talk) and r.status in (C.REHINT, C.REMAPPED, C.TYPE_CHANGED,
                                                                     C.COMPILE_ERROR, C.COMPOSITE_DROPPED):
        t = r.old_talk.replace("\r", "\n")
        talk = f'<details><summary>Старый VTT Talk</summary><pre>{E(t)}</pre></details>'
    return (f'<div class="card"><div class="thumb">{svg}</div><div class="meta">'
            f'<div class="nm">{E(r.name)}</div><div class="sub">{E(ulabel(res, r.name))}</div>'
            f'<div class="sub">GID {r.gid}{group}</div>'
            f'<ul class="notes">{notes}</ul>{extra}{used_in_html(r)}{talk}</div></div>')


def section(id_, title, intro, body, count=None, open_=True):
    c = f' <span class="count">{count}</span>' if count is not None else ""
    return (f'<section id="{id_}"><details {"open" if open_ else ""}><summary><h2>{E(title)}{c}</h2></summary>'
            f'<p class="intro">{intro}</p>{body}</details></section>')


# ---------------------------------------------------------------- report

CSS = """
:root{--bg:#fbfaf8;--fg:#1d1d1b;--muted:#6b6a66;--line:#e3e1dc;--card:#fff;--accent:#2f5fd0;
--ok:#2e7d4f;--warn:#b26a00;--err:#c0392b;--info:#5a6b85;--glyph:#1d1d1b;--old:#d0452f;--chip:#efede8}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161615;--fg:#ecebe7;--muted:#9a9893;
--line:#2d2c2a;--card:#1f1e1c;--accent:#7ea2ff;--ok:#5cc28a;--warn:#e0a240;--err:#ff7a6b;--info:#9fb0c9;
--glyph:#ecebe7;--old:#ff7a6b;--chip:#2a2927}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 80px}
h1{font-size:26px;margin:0 0 4px}h2{display:inline;font-size:19px;margin:0}
.paths{color:var(--muted);font-size:13px;word-break:break-all}.paths b{color:var(--fg);font-weight:600}
.summary{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin:22px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat .n{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}.stat .l{color:var(--muted);font-size:13px}
.stat.todo .n{color:var(--warn)}.stat.good .n{color:var(--ok)}
section{border-top:1px solid var(--line);padding:14px 0}
summary{cursor:pointer}summary::marker{color:var(--muted)}
.count{background:var(--chip);border-radius:20px;padding:1px 9px;font-size:14px;font-weight:600;margin-left:6px}
.intro{color:var(--muted);margin:8px 0 14px;max-width:820px}
ol.todo li{margin:6px 0}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid var(--line);padding:6px 8px;
text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
.lvl{font-weight:650;white-space:nowrap}.lvl.ok{color:var(--ok)}.lvl.warn{color:var(--warn)}
.lvl.error{color:var(--err)}.lvl.info{color:var(--info)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.card{display:flex;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.thumb{flex:none}.meta{min-width:0;font-size:13px}.nm{font-weight:650;font-size:14px;word-break:break-all}
.sub{color:var(--muted)}ul.notes{margin:6px 0;padding-left:16px}.used{color:var(--warn);margin:4px 0}
svg.g{display:block;max-width:190px}svg.g .new{fill:var(--glyph);fill-opacity:.85}
svg.g .old{fill:none;stroke:var(--old);stroke-width:14;stroke-dasharray:28 16}
svg.g .base{stroke:var(--line);stroke-width:4}svg.g .lost{fill:var(--old)}
svg.g .lbl{fill:var(--old);font-family:system-ui;font-weight:700;paint-order:stroke;stroke:var(--card);stroke-width:12}
a{color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:5px}.chip{background:var(--chip);border-radius:6px;padding:2px 7px;
font-size:13px;font-family:ui-monospace,Menlo,Consolas,monospace}
pre{background:var(--chip);padding:8px;border-radius:6px;font-size:12px;overflow:auto;max-height:260px}
textarea{width:100%;font:12px ui-monospace,Menlo,monospace;background:var(--chip);color:var(--fg);
border:1px solid var(--line);border-radius:6px;padding:6px}
details.copy{margin-top:8px}h3{font-size:15px;margin:18px 0 8px}.muted{color:var(--muted)}
.legend{font-size:13px;color:var(--muted)}.legend i{display:inline-block;width:22px;height:0;
border-top:3px dashed var(--old);vertical-align:middle;margin-right:4px}
@media (max-width:520px){.grid{grid-template-columns:1fr}}
"""


def build(res: C.TransferResult) -> str:
    new_d, old_d = Drawer(res.out_font), Drawer(res.src_font)
    hp = getattr(res, "hprj", None)
    G = res.glyphs
    st = lambda *s: [r for r in G.values() if r.status in s]

    transferred = st(C.TRANSFERRED)
    remapped = st(C.REMAPPED)
    rehint = st(C.REHINT, C.COMPILE_ERROR)
    dropped = st(C.COMPOSITE_DROPPED)
    type_changed = st(C.TYPE_CHANGED)
    tc_simple = [r for r in type_changed if r.kind == "simple"]
    tc_comp = [r for r in type_changed if r.kind != "simple"]
    new_simple = sorted(st(C.NEW_SIMPLE), key=lambda r: -len(r.used_in))
    twins = st(C.NEW_TWIN)
    new_comp = st(C.NEW_COMPOSITE)
    new_comp_ready = [r for r in new_comp if not r.unhinted_components]
    new_comp_wait = [r for r in new_comp if r.unhinted_components]
    changed_unhinted = st(C.CHANGED_UNHINTED)
    geom = [r for r in transferred if r.geometry_changed]
    relinked = [r for r in transferred + st(C.UNHINTED_SAME) if r.kind == "composite" and r.notes]
    removed_hinted = [r for r in res.removed if r.hinted_in_source]
    must_hint = sorted(rehint + tc_simple, key=lambda r: -len(r.used_in))
    hinted_src_total = sum(1 for r in G.values() if r.hinted_in_source and r.status != C.NEW_TWIN) + len(removed_hinted)

    stem = res.output_path.name
    out = [f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Перенос VTT-хинтов</title><style>{CSS}</style></head><body><main>"]
    out.append(f"<h1>Перенос VTT-хинтов: {E(stem)}</h1>")
    out.append(f"<div class='paths'>Исходник: <b>{E(str(res.source_path))}</b><br>"
               f"Новая версия: <b>{E(str(res.target_path))}</b><br>"
               f"Результат: <b>{E(str(res.output_path))}</b><br>"
               + (f"Проект автохинтера: <b>{E(str(hp.output))}</b> (из {E(str(hp.source))})<br>" if hp else "") +
               f"{datetime.datetime.now():%Y-%m-%d %H:%M} · vtt-transfer {__version__}"
               f"{' · скомпилировано vttcompilepy' if res.compiled else ' · без компиляции'}</div>")

    stats = [
        ("good", len(transferred), f"хинтованных глифов перенесено (из {hinted_src_total})"),
        ("good", len(remapped), "перенесено с ремапом точек"),
        ("todo", len(must_hint), "перехинтовать"),
        ("todo" if dropped else "good", len(dropped), "композитов: восстановить свои инструкции"),
        ("good", len(twins), "новых глифов получили хинты двойника"),
        ("todo", len(new_simple), "новых простых глифов без хинтов"),
        ("todo" if new_comp_wait else "good", len(new_comp), f"новых композитов ({len(new_comp_wait)} ждут компоненты)"),
        ("", len(res.removed), "глифов удалено"),
    ]
    out.append("<div class='summary'>" + "".join(
        f"<div class='stat {c}'><div class='n'>{n}</div><div class='l'>{E(l)}</div></div>" for c, n, l in stats) + "</div>")

    # what to do
    errs = [c for c in res.checks if c.level == "error"]
    todo = []
    todo.append(f"Открыть <b>{E(stem)}</b> в VTT, выполнить <i>Compile everything</i> и убедиться, что ошибок нет.")
    if errs:
        todo.append(f"<b>Есть ошибки проверки ({len(errs)})</b> — см. раздел «Проверки» ниже, до начала работы.")
    if must_hint:
        todo.append(f"<a href='#rehint'>Перехинтовать {len(must_hint)} глифов</a>, у которых изменилась структура "
                    "точек: хинты с них сняты, старый VTT Talk приложен к каждой карточке.")
    if twins:
        todo.append(f"<a href='#twins'>Глянуть {len(twins)} новых глифов</a>, которым хинты скопированы с "
                    "идентичного или сдвинутого по X старого глифа.")
    if dropped:
        todo.append(f"<a href='#dropped'>Восстановить инструкции у {len(dropped)} композитов</a> "
                    "(обычно позиционирование диакритики): компоненты хинтованы, сняты только команды самого композита.")
    if new_simple:
        todo.append(f"<a href='#new'>Отхинтовать {len(new_simple)} новых простых глифов</a>. "
                    "Character groups назначены автоматически — проверьте.")
    if new_comp_wait:
        todo.append(f"<a href='#newcomp'>{len(new_comp_wait)} новых композитов</a> станут хинтованными, когда будут "
                    "отхинтованы их компоненты.")
    if new_comp_ready:
        todo.append(f"Просмотреть {len(new_comp_ready)} новых композитов из уже хинтованных компонентов — "
                    "обычно дохинтовка не нужна, но стоит проверить положение диакритики на мелких кеглях.")
    if hp:
        n_add = sum(len(c.added) for c in hp.classes)
        todo.append(f"<a href='#hprj'>Проверить обновлённый {E(hp.output.name)}</a>: {n_add} новых глифов "
                    "добавлены в классы автохинтера.")
    if remapped:
        todo.append(f"<a href='#remapped'>Проверить {len(remapped)} глифов с ремапом точек</a>.")
    if geom:
        todo.append(f"<a href='#geom'>Пробежать глазами {len(geom)} глифов</a>: структура та же, хинты перенесены, "
                    "но контур сдвинут.")
    out.append(section("todo", "Что сделать", "Порядок работы оператора.",
                       "<ol class='todo'>" + "".join(f"<li>{t}</li>" for t in todo) + "</ol>"))

    # checks
    lvl_names = {"ok": "OK", "info": "Инфо", "warn": "Внимание", "error": "Ошибка"}
    order = {"error": 0, "warn": 1, "info": 2, "ok": 3}
    rows = "".join(f"<tr><td class='lvl {c.level}'>{lvl_names[c.level]}</td><td>{E(c.title)}</td>"
                   f"<td>{E(c.detail)}</td></tr>" for c in sorted(res.checks, key=lambda c: order[c.level]))
    out.append(section("checks", "Проверки", "Совместимость шрифтов и проверка результата после компиляции.",
                       f"<table><tr><th></th><th>Проверка</th><th>Детали</th></tr>{rows}</table>"))

    legend = ("<p class='legend'><i></i>старый контур (пунктир), заливка — новый; "
              "красные точки — опорные точки старых хинтов, которых нет в новом контуре.</p>")
    body = legend + "<div class='grid'>" + "".join(
        card(res, r, new_d, old_d, show_old=r.source is not None) for r in must_hint) + "</div>"
    body += copybox([r.name for r in must_hint])
    out.append(section("rehint", "Перехинтовать", "Глифы, которые были хинтованы, но их точечная структура "
                       "изменилась так, что номера опорных точек восстановить нельзя. Хинты сняты.",
                       body, len(must_hint)))

    if dropped:
        body = "<div class='grid'>" + "".join(card(res, r, new_d) for r in dropped) + "</div>"
        body += copybox([r.name for r in dropped])
        out.append(section("dropped", "Композиты: восстановить собственные инструкции",
                           "У одного из компонентов изменилось число точек, поэтому команды композита "
                           "(SHC, CALL и т. п. после OFFSET) ссылались бы не на те точки и контуры. "
                           "Они сняты; сборка и хинты компонентов на месте. Старый код приложен.",
                           body, len(dropped)))

    if twins:
        rows = "".join(f"<tr><td>{E(r.name)}</td><td>← {E(r.source)}</td><td>{E('; '.join(r.notes))}</td></tr>"
                       for r in twins)
        out.append(section("twins", "Новые глифы с хинтами двойника", "Точки совпадают с хинтованным глифом "
                           "старой версии — точно (переименованная копия) или со сдвигом по X (например, "
                           "комбинируемый акцент из спейсингового). Во втором случае перенесены только Y-хинты.", f"<table>{rows}</table>", len(twins), open_=False))

    # new simple, by script
    by_script: dict = {}
    for r in new_simple:
        by_script.setdefault(script_of(res, r.name), []).append(r)
    body = ""
    for scr, items in sorted(by_script.items(), key=lambda x: -len(x[1])):
        cards = "".join(card(res, r, new_d, extra=f"<div class='sub'>{E(r.group_reason)}</div>") for r in items)
        body += f"<h3>{E(scr)} · {len(items)}</h3><div class='grid'>{cards}</div>"
    body += copybox([r.name for r in new_simple])
    out.append(section("new", "Новые простые глифы", "Хинтов нет; внутри раздела сначала те, что используются в большем числе композитов. Группа (character group) назначена по аналогии "
                       "с глифами той же категории в исходнике.", body, len(new_simple)))

    body = ""
    if new_comp_wait:
        cards = "".join(card(res, r, new_d, extra="<div class='sub'>ждёт: " + E(", ".join(r.unhinted_components))
                             + "</div>") for r in new_comp_wait)
        body += f"<h3>Ждут нехинтованные компоненты · {len(new_comp_wait)}</h3><div class='grid'>{cards}</div>"
        waiting = sorted({c for r in new_comp_wait for c in r.unhinted_components})
        body += f"<p class='muted'>Компоненты, от которых они зависят ({len(waiting)}):</p>" + chips(waiting)
    body += f"<h3>Из хинтованных компонентов · {len(new_comp_ready)}</h3>" + chips([r.name for r in new_comp_ready])
    body += copybox([r.name for r in new_comp])
    out.append(section("newcomp", "Новые композиты", "Код сборки (OFFSET) сгенерирован по новому шрифту в стиле "
                       f"исходника ({E(res.composite_style)}). Компоненты несут свои хинты, поэтому композит "
                       "хинтован, если хинтованы все его компоненты.", body, len(new_comp)))

    if remapped:
        cards = []
        for r in remapped:
            mp = ", ".join(f"{a}→{b}" for a, b in sorted(r.mapping.items()))
            cards.append(card(res, r, new_d, old_d, show_old=True,
                              extra=f"<div class='sub'>точки: {E(mp) or 'номера не изменились'}</div>"))
        out.append(section("remapped", "Перенесено с ремапом точек", "Контур изменился, но все опорные точки "
                           "нашлись в новом контуре по точным координатам; номера в VTT Talk переписаны, "
                           "ассемблер пересобран компилятором. Проверьте визуально.",
                           "<div class='grid'>" + "".join(cards) + "</div>", len(remapped)))

    if tc_comp:
        body = "<div class='grid'>" + "".join(card(res, r, new_d, extra=(
            "<div class='sub'>ждёт: " + E(", ".join(r.unhinted_components)) + "</div>" if r.unhinted_components
            else "<div class='sub'>все компоненты хинтованы</div>")) for r in tc_comp) + "</div>"
        out.append(section("typechanged", "Стали композитами", "Раньше были простыми глифами со своими хинтами, "
                           "теперь собраны из компонентов.", body, len(tc_comp)))

    if relinked:
        rows = "".join(f"<tr><td>{E(r.name)}</td><td>{E('; '.join(r.notes))}</td></tr>" for r in relinked)
        out.append(section("relinked", "Композиты с изменённой сборкой", "Хинты перенесены, код сборки (OFFSET) "
                           "обновлён по новому шрифту.", f"<table>{rows}</table>", len(relinked), open_=False))

    if geom:
        out.append(section("geom", "Контур сдвинут при той же структуре", "Хинты перенесены как есть. "
                           "Стоит посмотреть на мелких кеглях.", chips([r.name for r in geom]) +
                           copybox([r.name for r in geom]), len(geom), open_=False))

    if changed_unhinted:
        out.append(section("changedunhinted", "Структура изменилась, хинтов не было и раньше",
                           "Ничего не потеряно.", chips([r.name for r in changed_unhinted]),
                           len(changed_unhinted), open_=False))

    body = ""
    if res.renames:
        body += "<h3>Переименования</h3><table>" + "".join(
            f"<tr><td>{E(a)}</td><td>→ {E(b)}</td></tr>" for a, b in res.renames.items()) + "</table>"
    body += "<h3>Удалены из новой версии</h3>" + chips(
        [r.name + (" (был хинтован)" if r.hinted_in_source else "") for r in res.removed])
    out.append(section("names", "Переименования и удаления", "Глифы сопоставляются по именам; переименования "
                       "определены по Unicode и совпадению структуры.", body,
                       len(res.renames) + len(res.removed), open_=False))

    out.append(section("unhinted", "Все нехинтованные простые глифы результата",
                       "Включая те, что не были хинтованы и в исходнике.",
                       chips(res.still_unhinted_simple) + copybox(res.still_unhinted_simple),
                       len(res.still_unhinted_simple), open_=False))

    rows = "".join(f"<tr><td>{g}</td><td>{E(' '.join(ex))}</td>"
                   f"<td>{sum(1 for r in G.values() if r.group == g and r.group_reason)}</td></tr>"
                   for g, ex in sorted(res.groups.items()))
    out.append(section("groups", "Character groups", "Номера групп из TSI5 исходника и примеры глифов. "
                       "Последняя колонка — сколько глифов получили группу автоматически.",
                       f"<table><tr><th>Группа</th><th>Примеры в исходнике</th><th>Назначено</th></tr>{rows}</table>",
                       open_=False))

    if hp:
        rows = ""
        for c in hp.classes:
            parts = []
            if c.added:
                parts.append(f"<b>добавлены ({len(c.added)}):</b> " + E(" ".join(c.added)))
            if c.renamed:
                parts.append("<b>переименованы:</b> " + E(", ".join(f"{a} → {b}" for a, b in c.renamed)))
            if c.removed:
                parts.append("<b>удалены (нет в новой версии):</b> " + E(" ".join(c.removed)))
            if c.now_composite:
                parts.append("<b>теперь композиты, оставлены в списке:</b> " + E(" ".join(c.now_composite)))
            rows += (f"<tr><td><b>{E(c.name)}</b></td><td>{c.group if c.group is not None else '—'}</td>"
                     f"<td>{c.count}</td><td>{'<br>'.join(parts) or '<span class=muted>без изменений</span>'}</td></tr>")
        body = ("<table><tr><th>Класс</th><th>Группа TSI5</th><th>Глифов</th><th>Изменения</th></tr>"
                f"{rows}</table>")
        if hp.unassigned:
            body += ("<p class='muted'>Новые глифы, чьей группе не соответствует ни один класс (не добавлены):</p>"
                     + chips([f"{n} (гр. {g})" for n, g in hp.unassigned]))
        if hp.files:
            body += "<p class='muted'>" + "; ".join(E(f"{f}: {a} → {b}") for f, a, b in hp.files) + "</p>"
        out.append(section("hprj", "Проект автохинтера (.hprj)",
                           "Классы сопоставлены с группами TSI5 по большинству глифов класса. Новые простые глифы "
                           "добавлены в класс своей группы; глифы группы 0 (Other) в классы не входят.",
                           body, sum(len(c.added) for c in hp.classes)))

    tech = "<ul>" + "".join(f"<li>{E(a)}</li>" for a in res.actions) + "</ul>"
    if res.compile_log:
        tech += "<h3>Сообщения компилятора</h3><pre>" + E("\n".join(res.compile_log)) + "</pre>"
    out.append(section("tech", "Технические детали", "Что именно было сделано со шрифтом.", tech, open_=False))
    out.append("</main></body></html>")
    return "".join(out)
