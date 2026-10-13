"""VTT Control Program (TSI1 extraPrograms['cvt']): one parser and a formatting-preserving editor.

The parser is a port of the scanner and grammar of Microsoft's compiler (VisualTrueType
`src/CvtManager.cpp`, `PrivateControlValueTable::Compile`):

    program  = {ASM} {(GROUP name ["spacing"] | CATEGORY name) {ASM}}
               {setting {ASM}} {cvt}
    setting  = InstructionsOn @lo..hi | DropOutCtrlOff @n | ScanCtrl = n | ScanType = n
             | CvtCutIn = px {, px @ppem} | ClearTypeCtrl = n | LinearAdvanceWidths = n | FPgmBias = n
    cvt      = {attribute {ASM}} n ":" value {ASM} [("=" | "~") parent @ppem] {ASM}
               {(Delta | BDelta | GDelta) "(" px @ppems [% color] {, px @ppems [% color]} ")"} {ASM}
    ASM      = ASM "(" "code" ")"

It is token based: line breaks and indentation mean nothing, attributes (group, color, direction,
category) are sticky until changed, comments nest. Keywords and attribute names are case-insensitive.
Numbers may be expressions (`+ - * /`, parentheses); pixel amounts are 26.6 (`2.5`).

`parse` never raises: problems go to `Program.errors` and the parser resyncs at the next declaration.
Line numbers are 0-based, in the line split of `\\r\\n`, `\\r` or `\\n` (VTT keeps sources with CR).

`set_cvt` and `set_setting` edit the text in place: only the edited tokens change; comments, spacing,
line ends and everything else stay as they are.
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field

# Built-in attribute values, in the compiler's order. A group's number is the one TSI5 stores; groups
# declared with GROUP get 8, 9, … in declaration order.
GROUPS = ("AnyGroup", "Other", "UpperCase", "LowerCase", "Figure", "Reserved1", "Reserved2", "Reserved3")
COLORS = ("AnyColor", "Black", "Grey", "White")
DIRECTIONS = ("AnyDirection", "X", "Y", "Diag")
CATEGORIES = ("AnyCategory", "Distance", "StraightStroke", "RoundStroke", "LSB", "RSB", "BlackBodyWidth",
              "SerifThin", "SerifHeight", "SerifExt", "SerifCurveHeight", "SerifOther", "SerifBottom",
              "SquareHeight", "RoundHeight", "ItalicRun", "ItalicRise", "BendRadius", "BranchBendRadius",
              "ScoopDepth", "NewStraight", "NewDiagonal", "NewAnyFeature")
FIRST_USER_GROUP = len(GROUPS)

SETTINGS = ("FPgmBias", "InstructionsOn", "DropOutCtrlOff", "ScanCtrl", "ScanType", "CvtCutIn",
            "ClearTypeCtrl", "LinearAdvanceWidths")
DELTAS = ("Delta", "BDelta", "GDelta")
KEYWORDS = ("GROUP", "CATEGORY", *SETTINGS, *DELTAS, "ASM")
_KEYWORD = {k.upper(): k for k in KEYWORDS}

MAX_PPEM = 255
SHORT_MAX = 32767
ONE = 64            # 26.6


# ---------------------------------------------------------------- result

@dataclass
class Program:
    """A parsed Control Program. Every field is plain data (json.dumps-able; `cvts` keys are ints).

    declarations  [{kind: "GROUP"|"CATEGORY", name, spacing, number, line}] in source order; `number` of
                  a GROUP is its TSI5 number (8, 9, …), of a CATEGORY its attribute number
    groups        {name: TSI5 number} of all groups, built-in and declared
    settings      {key: {value, line}}: InstructionsOn [lo, hi]; DropOutCtrlOff, ScanCtrl, ScanType,
                  ClearTypeCtrl, LinearAdvanceWidths, FPgmBias n; CvtCutIn [[px, None], [px, ppem], …]
    asm           [{line, code}] of every ASM("…") statement
    cvts          {n: {value, op, parent, ppem, deltas, group, color, direction, category, name, note,
                  line, absolute}}: `value` as written (relative for `~`); op "~", "=" or None;
                  `absolute` = value, plus the parent's absolute for `~`; deltas [{kind, amount, ppems,
                  color}]; group/color/direction/category None when Any…; name/note the text of the
                  first/second comment on the CVT's line
    errors        [{line, col, message}]
    """
    declarations: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    asm: list = field(default_factory=list)
    cvts: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def as_json(self) -> dict:
        d = asdict(self)
        d["cvts"] = {str(k): v for k, v in d["cvts"].items()}
        return d


class ControlError(ValueError):
    pass


# ---------------------------------------------------------------- scanner

def blank_comments(text: str) -> str:
    """Comments replaced by spaces, line breaks kept, so line numbers and columns stay valid. VTT's
    comments nest (Microsoft's Control Program template has `/* … */` examples inside comments)."""
    out, depth, i, n = [], 0, 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "/*":
            depth += 1
            out.append("  ")
            i += 2
        elif two == "*/" and depth:
            depth -= 1
            out.append("  ")
            i += 2
        else:
            c = text[i]
            out.append(c if not depth or c in "\r\n" else " ")
            i += 1
    return "".join(out)


@dataclass
class _Tok:
    kind: str           # "num", "ident", "kw", "lit", "eot" or the punctuation itself ("..", "@", …)
    start: int
    end: int
    text: str = ""      # ident / keyword (canonical) / literal contents
    value: int = 0      # numbers: 26.6
    rational: bool = False
    hex: bool = False


class _Err(Exception):
    def __init__(self, pos: int, message: str):
        super().__init__(message)
        self.pos = pos


def _scan(text: str, errors: list) -> tuple[list[_Tok], list[tuple[int, int]]]:
    """Tokens and top-level comments (start, end). Scanner errors are recorded, the offending
    character skipped; an unclosed comment runs to the end."""
    toks, comments = [], []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c <= " ":
            i += 1
            continue
        if text.startswith("/*", i):
            start, depth = i, 0
            while i < n:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                    if not depth:
                        break
                else:
                    i += 1
            if depth:
                errors.append((start, "comment opened but not closed"))
            comments.append((start, i))
            continue
        start = i
        if c.isascii() and c.isdigit():
            if c == "0" and i + 1 < n and text[i + 1] in "xX":
                i += 2
                while i < n and text[i] in "0123456789abcdefABCDEF":
                    i += 1
                toks.append(_Tok("num", start, i, value=int(text[start + 2:i] or "0", 16) * ONE, hex=True))
                continue
            while i < n and text[i].isascii() and text[i].isdigit():
                i += 1
            value, rational = int(text[start:i]) * ONE, False
            if i < n and text[i] == "." and not text.startswith("..", i):
                i += 1
                d0 = i
                while i < n and text[i].isascii() and text[i].isdigit():
                    i += 1
                if i > d0:
                    dec, p10 = int(text[d0:i]), 10 ** (i - d0)
                    value += (dec * ONE + p10 // 2) // p10
                    rational = True
            toks.append(_Tok("num", start, i, value=value, rational=rational))
        elif c.isascii() and c.isalpha():
            while i < n and text[i].isascii() and text[i].isalnum():
                i += 1
            word = text[start:i]
            kw = _KEYWORD.get(word.upper())
            toks.append(_Tok("kw", start, i, text=kw) if kw else _Tok("ident", start, i, text=word))
        elif c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                errors.append((start, '" expected'))
                j = n - 1
            i = j + 1
            toks.append(_Tok("lit", start, i, text=text[start + 1:j]))
        elif text.startswith("..", i):
            i += 2
            toks.append(_Tok("..", start, i))
        elif c in ".,:;%@()+-*/=~[]":
            i += 1
            toks.append(_Tok({"[": "(", "]": ")"}.get(c, c), start, i))
        else:
            i += 1
            errors.append((start, f"invalid character {c!r}"))
    toks.append(_Tok("eot", n, n))
    return toks, comments


# ---------------------------------------------------------------- parser

def _num(v26: int, rational: bool):
    return v26 / ONE if rational else v26 // ONE


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.errors_at: list[tuple[int, str]] = []
        self.toks, self.comments = _scan(text, self.errors_at)
        self.i = 0
        self.attrs = {"group": {g.upper(): g for g in GROUPS}, "color": {c.upper(): c for c in COLORS},
                      "direction": {d.upper(): d for d in DIRECTIONS},
                      "category": {c.upper(): c for c in CATEGORIES}}
        self.group_numbers = {g: k for k, g in enumerate(GROUPS)}
        self.next_number = {"GROUP": FIRST_USER_GROUP, "CATEGORY": len(CATEGORIES)}
        self.prog = Program()
        self.spans: dict = {"cvts": {}, "settings": {}, "declarations": [], "first": None}
        self.ctx = {"group": None, "color": None, "direction": None, "category": None}
        self.group_tok = None

    # tokens
    @property
    def tok(self) -> _Tok:
        return self.toks[self.i]

    def next(self) -> _Tok:
        t = self.toks[self.i]
        if t.kind != "eot":
            self.i += 1
        return t

    def expect(self, kind: str, what: str | None = None) -> _Tok:
        if self.tok.kind != kind:
            raise _Err(self.tok.start, f"{what or repr(kind)} expected")
        return self.next()

    def is_kw(self, *names) -> bool:
        return self.tok.kind == "kw" and self.tok.text in names

    def prev_end(self) -> int:
        return self.toks[self.i - 1].end if self.i else 0

    # expressions, as the compiler: (type, 26.6 value); type "natural" or "rational"
    def expression(self):
        sign = 1
        if self.tok.kind in ("+", "-"):
            if self.next().kind == "-":
                sign = -1
        rat, v = self.term()
        v *= sign
        while self.tok.kind in ("+", "-"):
            op = self.next().kind
            rat2, v2 = self.term()
            v = v + v2 if op == "+" else v - v2
            rat = rat or rat2
            if not -SHORT_MAX - 1 <= v / ONE <= SHORT_MAX:
                raise _Err(self.tok.start, "result out of range")
        return rat, v

    def term(self):
        rat, v = self.factor()
        while self.tok.kind in ("*", "/"):
            op = self.next().kind
            rat2, v2 = self.factor()
            if op == "*":
                v, rat = (v * v2 + ONE // 2) >> 6, rat or rat2
            else:
                if v2 == 0:
                    raise _Err(self.tok.start, "division by zero")
                rat = rat or rat2 or v % v2 != 0
                v = ((v << 7) + v2) // (v2 << 1)
        return rat, v

    def factor(self):
        t = self.tok
        if t.kind == "num":
            self.next()
            return t.rational, t.value
        if t.kind == "(":
            self.next()
            r = self.expression()
            self.expect(")", "')'")
            return r
        raise _Err(t.start, "number or '(' expected")

    def natural(self, what: str, lo: int, hi: int) -> int:
        start = self.tok.start
        rat, v = self.expression()
        if rat or v % ONE:
            raise _Err(start, f"{what} expected (an integer in range {lo} through {hi})")
        v //= ONE
        if not lo <= v <= hi:
            raise _Err(start, f"{what} out of range (must be in range {lo} through {hi})")
        return v

    def pixels(self, what: str, lo: float, hi: float):
        start = self.tok.start
        rat, v = self.expression()
        if not lo <= v / ONE <= hi:
            raise _Err(start, f"{what} out of range ({lo} through {hi})")
        return _num(v, rat or v % ONE != 0)

    def ppem_ranges(self, low: int = 0, high: int = MAX_PPEM) -> tuple[list[int], bool, int, int]:
        """After '@': range {; range}, each above `low`. Returns (ppems, is_range, start, end)."""
        start, ppems, is_range = self.tok.start, [], False
        while True:
            a = self.natural("ppem size", low + 1, high)
            b = a
            if self.tok.kind == "..":
                self.next()
                b = self.natural("ppem size", a + 1, high)
                is_range = True
            ppems += range(a, b + 1)
            low = b
            if self.tok.kind != ";":
                break
            self.next()
            is_range = True
        return ppems, is_range, start, self.prev_end()

    def at_ppem(self, what: str, low: int = 0):
        """'@' n → (n, start of n, end)."""
        self.expect("@", f"'@' and {what}")
        ppems, is_range, s, e = self.ppem_ranges(low)
        if is_range:
            raise _Err(s, f"{what} expected (a single ppem size)")
        return ppems[0], s, e

    def inline(self):
        while self.is_kw("ASM"):
            self.next()
            self.expect("(", "'('")
            lit = self.expect("lit", "actual TrueType code in quotes")
            self.expect(")", "')'")
            self.prog.asm.append(dict(line=self.line(lit.start), code=lit.text))

    # helpers
    def line(self, pos: int) -> int:
        return bisect.bisect_right(self.line_starts, pos) - 1

    def error(self, pos: int, message: str):
        self.prog.errors.append(dict(line=self.line(pos), col=pos - self.line_starts[self.line(pos)],
                                     message=message))

    def resync(self, advance: bool):
        """Skip to the next token that can start a declaration: a keyword, a known attribute name or
        `n :`. advance: skip the current token first (the failed declaration consumed nothing)."""
        if advance:
            self.next()
        while self.tok.kind != "eot":
            t = self.tok
            if t.kind == "kw" and t.text != "ASM" and t.text not in DELTAS:
                return
            if t.kind == "ident" and self.attribute(t.text):
                return
            if t.kind == "num" and self.toks[self.i + 1].kind == ":":
                return
            self.next()

    def attribute(self, name: str):
        for kind, names in self.attrs.items():
            if name.upper() in names:
                return kind, names[name.upper()]
        return None

    # grammar
    def run(self) -> Program:
        text = self.text
        self.line_starts = [0]
        i = 0
        while i < len(text):
            c = text[i]
            if c == "\r" and text.startswith("\r\n", i):
                i += 1
            if c in "\r\n":
                self.line_starts.append(i + 1)
            i += 1
        for pos, msg in self.errors_at:
            self.error(pos, msg)
        self.prog.groups = dict(self.group_numbers)
        phase = 0       # 0 declarations, 1 settings, 2 cvts
        while self.tok.kind != "eot":
            start = self.i
            try:
                self.inline()
                t = self.tok
                if t.kind == "eot":
                    break
                if self.spans["first"] is None:
                    self.spans["first"] = t.start
                if self.is_kw("GROUP", "CATEGORY"):
                    if phase > 0:
                        self.error(t.start, f"{t.text} must come before the settings and CVTs")
                    self.declaration()
                elif t.kind == "kw" and t.text in SETTINGS:
                    if phase > 1:
                        self.error(t.start, f"{t.text} must come before the CVTs")
                    phase = max(phase, 1)
                    self.setting()
                else:
                    phase = 2
                    self.cvt()
            except _Err as e:
                self.error(e.pos, str(e))
                self.resync(self.i == start)
        self.prog.groups = dict(self.group_numbers)
        self._absolute()
        self._comments()
        return self.prog

    def declaration(self):
        kw = self.next()
        name = self.expect("ident", f"{kw.text} name")
        spacing = None
        if kw.text == "GROUP" and self.tok.kind == "lit":
            spacing = self.next().text
        kind = "group" if kw.text == "GROUP" else "category"
        if self.attribute(name.text):
            raise _Err(name.start, f"{name.text} is already defined")
        number = self.next_number[kw.text]
        self.next_number[kw.text] += 1
        self.attrs[kind][name.text.upper()] = name.text
        if kind == "group":
            self.group_numbers[name.text] = number
        self.prog.declarations.append(dict(kind=kw.text, name=name.text, spacing=spacing, number=number,
                                           line=self.line(kw.start)))
        self.spans["declarations"].append((kw.start, self.prev_end()))

    def setting(self):
        kw = self.next()
        key = kw.text
        if key in self.prog.settings:
            raise _Err(kw.start, f"{key} already defined")
        if key == "InstructionsOn":
            self.expect("@", "'@' and a range of ppem sizes")
            ppems, is_range, s, _ = self.ppem_ranges(-1, SHORT_MAX)
            if not is_range or ppems != list(range(ppems[0], ppems[-1] + 1)):
                raise _Err(s, "range of ppem sizes expected (example: @8..2047)")
            value = [ppems[0], ppems[-1]]
        elif key == "DropOutCtrlOff":
            if "ScanCtrl" in self.prog.settings or "ScanType" in self.prog.settings:
                self.error(kw.start, "cannot use DropOutCtrlOff together with ScanCtrl or ScanType")
            value = self.at_ppem("drop-out control turn-off ppem size", -1)[0]
        elif key == "CvtCutIn":
            self.expect("=", "'='")
            value = [[self.pixels("cut-in pixel amount", 0, 255), None]]
            while self.tok.kind == ",":
                self.next()
                if len(value) >= 4:
                    raise _Err(self.tok.start, "too many cvt cut-ins (cannot have more than 4)")
                s = self.tok.start
                px = self.pixels("cut-in pixel amount", 0, 255)
                if px >= value[-1][0]:
                    self.error(s, "a cut-in must be smaller than the one before")
                value.append([px, self.at_ppem("ppem size")[0]])
        else:
            if key in ("ScanCtrl", "ScanType") and "DropOutCtrlOff" in self.prog.settings:
                self.error(kw.start, f"cannot use {key} together with DropOutCtrlOff")
            self.expect("=", "'='")
            lo, hi = {"ScanCtrl": (0, 16383), "ScanType": (1, 6), "ClearTypeCtrl": (0, 1),
                      "LinearAdvanceWidths": (0, 1), "FPgmBias": (0, 32767)}[key]
            value = self.natural(f"value for {key}", lo, hi)
        self.prog.settings[key] = dict(value=value, line=self.line(kw.start))
        self.spans["settings"][key] = (kw.start, self.prev_end())

    def cvt(self):
        while self.tok.kind == "ident":
            t = self.tok
            a = self.attribute(t.text)
            if a is None:
                raise _Err(t.start, f"unknown attribute {t.text!r}")
            self.next()
            kind, name = a
            self.ctx[kind] = None if name.startswith("Any") else name
            if kind == "group":
                self.group_tok = t.start
            self.inline()
        t = self.tok
        if t.kind != "num":
            raise _Err(t.start, "CVT number expected" if t.kind != "kw" else f"{t.text} not expected here")
        n = self.natural("cvt number", 0, SHORT_MAX)
        num_span = (t.start, self.prev_end())
        self.expect(":", "':'")
        vs = self.tok.start
        rat, v = self.expression()
        if rat or v % ONE:
            raise _Err(vs, "CVT value expected (an integer in font units)")
        value, value_span = v // ONE, (vs, self.prev_end())
        if self.tok.kind == "num" and self.tok.hex:     # old format: hexadecimal attributes
            self.next()
        end = self.prev_end()       # of the declaration proper, without the ASM statements after it
        self.inline()
        op = parent = ppem = None
        inh_span = ppem_span = None
        if self.tok.kind in ("=", "~"):
            o = self.next()
            op = o.kind
            ps = self.tok.start
            parent = self.natural("parent cvt number", 0, SHORT_MAX)
            if parent not in self.prog.cvts:
                self.error(ps, "parent cvt not defined (must be defined before its children)")
            ppem, s, e = self.at_ppem("break ppem size")
            inh_span, ppem_span, end = (o.start, e), (s, e), e
            self.inline()
        deltas, delta_span, kinds = [], None, set()
        while self.tok.kind == "kw" and self.tok.text in DELTAS:
            kw = self.next()
            if kw.text in kinds:
                self.error(kw.start, f"cannot have more than one {kw.text} per control value")
            kinds.add(kw.text)
            self.expect("(", "'('")
            low = (ppem or 0) - 1
            while True:
                s = self.tok.start
                amount = self.pixels("delta amount", -8, 8)
                if amount == 0:
                    self.error(s, "delta amount cannot be 0")
                self.expect("@", "'@' and ppem sizes")
                ppems, _, _, _ = self.ppem_ranges(low)
                low = ppems[-1]
                color = None
                if self.tok.kind == "%":
                    self.next()
                    color = self.natural("delta color", 0, 255)
                deltas.append(dict(kind=kw.text, amount=amount, ppems=ppems, color=color))
                if self.tok.kind != ",":
                    break
                self.next()
            self.expect(")", "')'")
            delta_span = (delta_span[0] if delta_span else kw.start, self.prev_end())
            end = self.prev_end()
            self.inline()
        if n in self.prog.cvts:
            self.error(t.start, f"cvt {n} already defined")
            return
        self.prog.cvts[n] = dict(value=value, op=op, parent=parent, ppem=ppem, deltas=deltas,
                                 group=self.ctx["group"], color=self.ctx["color"],
                                 direction=self.ctx["direction"], category=self.ctx["category"],
                                 name=None, note=None, line=self.line(t.start), absolute=value)
        self.spans["cvts"][n] = dict(num=num_span, value=value_span, inherit=inh_span, ppem=ppem_span,
                                     deltas=delta_span, end=end, next=self.tok.start,
                                     group_line=self.line(self.group_tok) if self.group_tok is not None
                                     else self.line(t.start))

    def _absolute(self):
        cvts = self.prog.cvts
        for n in cvts:
            seen, k, total = set(), n, 0
            while k in cvts and k not in seen:
                seen.add(k)
                c = cvts[k]
                total += c["value"]
                if c["op"] != "~":
                    break
                k = c["parent"]
            cvts[n]["absolute"] = total

    def _comments(self):
        for n, sp in self.spans["cvts"].items():
            line = self.prog.cvts[n]["line"]
            found = [(s, e) for s, e in self.comments
                     if sp["num"][0] < s < sp["next"] and self.line(s) == line]
            sp["comments"] = found
            texts = [self.text[s + 2:e - 2].strip() for s, e in found]
            self.prog.cvts[n]["name"] = texts[0] if texts else None
            self.prog.cvts[n]["note"] = texts[1] if len(texts) > 1 else None


def _parse(text: str) -> tuple[Program, dict]:
    p = _Parser(text)
    p.run()
    return p.prog, p.spans


def parse(text: str) -> Program:
    """Parse a Control Program. Never raises; see `Program.errors`."""
    return _parse(text)[0]


def group_line(text: str, n: int) -> int | None:
    """Line of the attribute that set CVT n's group (the CVT's own line if no group was named)."""
    sp = _parse(text)[1]["cvts"].get(n)
    return sp["group_line"] if sp else None


# ---------------------------------------------------------------- editing

def newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"


def _fmt(x) -> str:
    if isinstance(x, float):
        if x.is_integer():
            return str(int(x))
        return f"{x:.6f}".rstrip("0")
    return str(x)


def _fmt_ppems(ppems) -> str:
    ppems = sorted(set(ppems))
    runs, out = [], []
    for p in ppems:
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    for a, b in runs:
        out.append(str(a) if a == b else f"{a}..{b}")
    return ";".join(out)


def _normalize_deltas(deltas) -> list[dict]:
    """[(amount, ppems)] or [{kind, amount, ppems, color}] → entries the compiler accepts: per kind
    ascending ppems, one entry per run of equal amount (and color)."""
    per_kind: dict[str, dict[int, tuple]] = {}
    for d in deltas:
        if isinstance(d, dict):
            kind, amount, ppems, color = d.get("kind", "Delta"), d["amount"], d["ppems"], d.get("color")
        else:
            kind, (amount, ppems), color = "Delta", d, None
        if kind not in DELTAS:
            raise ControlError(f"unknown delta kind {kind!r}")
        if isinstance(ppems, int):
            ppems = [ppems]
        if not amount:
            raise ControlError("delta amount cannot be 0")
        if round(amount * ONE) != amount * ONE:
            raise ControlError(f"delta amount {amount} is not a multiple of 1/64")
        m = per_kind.setdefault(kind, {})
        for p in ppems:
            if not 1 <= p <= MAX_PPEM:
                raise ControlError(f"ppem {p} out of range")
            if p in m and m[p] != (amount, color):
                raise ControlError(f"two {kind} amounts at {p} ppem")
            m[p] = (amount, color)
    out = []
    for kind in DELTAS:
        if kind not in per_kind:
            continue
        entries: list[dict] = []
        for p in sorted(per_kind[kind]):
            amount, color = per_kind[kind][p]
            if entries and (entries[-1]["amount"], entries[-1]["color"]) == (amount, color):
                entries[-1]["ppems"].append(p)
            else:
                entries.append(dict(kind=kind, amount=amount, ppems=[p], color=color))
        out += entries
    return out


def _render_deltas(entries: list[dict]) -> str:
    parts = []
    for kind in DELTAS:
        es = [e for e in entries if e["kind"] == kind]
        if es:
            items = [f"{_fmt(e['amount'])} @{_fmt_ppems(e['ppems'])}" + (f" %{e['color']}" if e["color"] is not None else "")
                     for e in es]
            parts.append(f"{kind}({', '.join(items)})")
    return " ".join(parts)


def _splice(text: str, edits: list[tuple[int, int, str]]) -> str:
    for s, e, new in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        text = text[:s] + new + text[e:]
    return text


def _comment(s: str) -> str:
    if "*/" in s or "/*" in s:
        raise ControlError("a comment cannot contain /* or */")
    return f"/* {s} */"


def set_cvt(text: str, n: int, *, value: int | None = None, ppem: int | None = None, deltas=None,
            name: str | None = None, note: str | None = None) -> str:
    """CVT n with a new value (as written: relative for `~`), break ppem, deltas or comments; the rest
    of the text is unchanged. None keeps a field. deltas: [] removes them, else [(amount, ppems)] or
    [{kind, amount, ppems, color}] replace all of them (written as one Delta(…) per kind). name / note
    replace or add the first / second comment on the CVT's line; note="" removes the second.
    Raises ControlError if CVT n is not defined (or ppem is given for a CVT without a parent)."""
    prog, spans = _parse(text)
    if n not in spans["cvts"]:
        raise ControlError(f"cvt {n} is not defined in the Control Program")
    sp = spans["cvts"][n]
    edits = []
    if value is not None:
        edits.append((*sp["value"], str(int(value))))
    if ppem is not None:
        if sp["ppem"] is None:
            raise ControlError(f"cvt {n} has no parent, so no break ppem")
        edits.append((*sp["ppem"], str(int(ppem))))
    if deltas is not None:
        rendered = _render_deltas(_normalize_deltas(deltas))
        if sp["deltas"]:
            s, e = sp["deltas"]
            if not rendered:
                while s > 0 and text[s - 1] in " \t":
                    s -= 1
            edits.append((s, e, rendered))
        elif rendered:
            at = (sp["inherit"] or sp["value"])[1]
            edits.append((at, at, " " + rendered))
    comments = sp["comments"]
    if name is not None and not comments:
        edits.append((sp["end"], sp["end"], " " + _comment(name) + (" " + _comment(note) if note else "")))
        note = None
    elif name is not None:
        edits.append((*comments[0], _comment(name)))
    if note is not None:
        if len(comments) > 1:
            s, e = comments[1]
            if note:
                edits.append((s, e, _comment(note)))
            else:
                while s > 0 and text[s - 1] in " \t":
                    s -= 1
                edits.append((s, e, ""))
        elif note:
            if not comments and name is None:
                raise ControlError(f"cvt {n} has no first comment to put the note after")
            at = comments[0][1] if comments else sp["end"]
            edits.append((at, at, " " + _comment(note)))
    return _splice(text, edits)


def _canonical_setting(key: str) -> str:
    for k in SETTINGS:
        if k.upper() == key.upper():
            return k
    raise ControlError(f"unknown setting {key!r}")


def render_setting(key: str, value) -> str:
    key = _canonical_setting(key)
    if key == "InstructionsOn":
        lo, hi = value
        return f"InstructionsOn @{int(lo)}..{int(hi)}"
    if key == "DropOutCtrlOff":
        return f"DropOutCtrlOff @{int(value)}"
    if key == "CvtCutIn":
        value = [v if isinstance(v, (list, tuple)) else (v, None) for v in value]
        if not 1 <= len(value) <= 4 or value[0][1] is not None or any(p is None for _, p in value[1:]):
            raise ControlError("CvtCutIn: 1 to 4 entries, the first without a ppem size, the others with one")
        return "CvtCutIn = " + ", ".join(_fmt(v) + ("" if p is None else f" @{int(p)}") for v, p in value)
    return f"{key} = {int(value)}"


def _line_bounds(text: str, pos: int) -> tuple[int, int, int]:
    """(start of the line, end before its line break, end after its line break) around pos."""
    s = max(text.rfind("\r", 0, pos), text.rfind("\n", 0, pos)) + 1
    e = s
    while e < len(text) and text[e] not in "\r\n":
        e += 1
    after = e + (2 if text.startswith("\r\n", e) else 1 if e < len(text) else 0)
    return s, e, after


def set_setting(text: str, key: str, value) -> str:
    """Replace a setting (InstructionsOn (lo, hi), DropOutCtrlOff n, ScanCtrl n, ScanType n,
    CvtCutIn [(px, None), (px, ppem), …], ClearTypeCtrl n, LinearAdvanceWidths n, FPgmBias n), add it on
    a line of its own after the declarations / other settings if missing, or remove it (value None)."""
    key = _canonical_setting(key)
    prog, spans = _parse(text)
    nl = newline(text)
    have = spans["settings"]
    if value is None:
        if key not in have:
            return text
        s, e = have[key]
        ls, le, after = _line_bounds(text, s)
        if not text[ls:s].strip() and not text[e:le].strip():
            return text[:ls] + text[after:]
        while s > 0 and text[s - 1] in " \t":
            s -= 1
        return text[:s] + text[e:]
    new = render_setting(key, value)
    if key in have:
        return _splice(text, [(*have[key], new)])
    others = [e for k, (s, e) in have.items() if k != "FPgmBias"] if key != "FPgmBias" else []
    anchor = max(others, default=None)
    if anchor is None and key != "FPgmBias" and "FPgmBias" in have:
        anchor = have["FPgmBias"][1]
    if anchor is None and spans["declarations"]:
        anchor = spans["declarations"][-1][1]
    if anchor is None:
        first = spans["first"]
        if first is None:
            return text + ("" if not text or text.endswith(("\r", "\n")) else nl) + new + nl
        ls, _, _ = _line_bounds(text, first)
        return text[:ls] + new + nl + text[ls:]
    _, le, _ = _line_bounds(text, anchor)
    return text[:le] + nl + new + text[le:]
