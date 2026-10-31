"""Verify guide claims (anastylos-guide-claims@1) against the exact session package; speak only what holds.

Offline, stdlib only. Architecture A2: a model proposes claims, this module accepts or rejects each one against
``session.json`` (whose SHA-256 must equal the hash recorded when the package was built), and the German text
that is spoken is rendered from accepted claims by fixed templates, never from model prose. A free-text answer
is first mapped to claims by a fixed lexicon (ids, cells, sites, order, basis, colour and name words); a
sentence it cannot map is rejected. One rejected claim rejects the whole answer (fail-closed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

CLAIMS_SCHEMA = "anastylos-guide-claims@1"
PACKAGE_SCHEMA = "anastylos-session-package@1"
TASK_SCHEMA_FILE = Path(__file__).resolve().parents[1] / "live" / "schemas" / "task.guide_claims.json"
BASES = ("automatic", "operator_declared")

# The only basis words a claim may carry (GUIDE_RULES 3, 5, 5a, 5b). Rule 5 names the basis of an order
# ("reverse of the observed removal order") without a fixed German phrase; this is the phrase the templates use.
SYSTEM_ASSIGNED = "vom System zugeordnet"
BY_OPERATOR = "laut Operator"
GROUP_STATEMENT = "laut Operator identisch, nicht vermessen"
REVERSE_ORDER = "umgekehrte Entnahmereihenfolge"
NOT_OBSERVED = "nicht beobachtet"
BASIS_WORDS = (SYSTEM_ASSIGNED, BY_OPERATOR, GROUP_STATEMENT, REVERSE_ORDER, NOT_OBSERVED)

NOT_PROVEN = "Das ist im Sitzungspaket nicht belegt; ich gebe diese Antwort nicht wieder."
UNKNOWN_SPEECH = "Das wurde in dieser Sitzung nicht beobachtet."

ID = re.compile(r"\b(?:obs|link|dec|store|grp|phase)-\d{4}\b")
CELL = re.compile(r"\b(?:cell|Zelle)[ -](\d{1,2})\b", re.IGNORECASE)
SITE = re.compile(r"\bW-[A-Z/]+\b|\bH\d+\b")
NEGATION = re.compile(r"\b(?:nicht|nie|kein|keine|not|never|no)\b", re.IGNORECASE)
UNCERTAIN = re.compile(r"nicht (?:\w+ )?(?:beobachtet|bestätigt|belegt)|not (?:\w+ )?(?:observed|confirmed)|unbekannt|"
                       r"\bunknown\b|weiß ich nicht|keine (?:Angabe|Information)", re.IGNORECASE)
COLOUR = re.compile(r"\b(?:grün|blau|rot|gelb|weiß|weiss|schwarz|grau|braun|lila|violett|orange|rosa)(?:e|er|es|en|em)?\b|"
                    r"\b(?:green|blue|red|yellow|white|black|grey|gray|brown|purple|pink)\b", re.IGNORECASE)
PART_NAME = re.compile(r"\w*(?:knopf|knöpf|taste|button|schraube|membran|d-pad|steuerkreuz|platine|kabel|rückwand|"
                       r"gehäuse|deckel|feder|abdeckung)\w*|\b(?:keys?|screws?|membranes?|cables?|boards?|springs?)\b",
                       re.IGNORECASE)
CERTAINTY = re.compile(r"\b(?:sicher|zweifelsfrei|eindeutig|garantiert|definitiv|zuverlässig|bestätigt|verifiziert|"
                       r"identifiziert|erkannt|certain(?:ly)?|definitely|confirmed|verified|identified|recogni[sz]ed)\b",
                       re.IGNORECASE)
LATER_OBSERVATION = re.compile(r"\baktuelle[nsm]? (?:Foto|Bild|Aufnahme)|\bjetzige[nsm]? (?:Foto|Bild)|\bzeigt jetzt\b|"
                               r"\bauf dem Foto von jetzt\b|\bcurrent (?:photo|image|picture)\b|\bnow shows\b",
                               re.IGNORECASE)
OBSERVED = re.compile(r"\b(?:beobachtet|gesehen|gefilmt|observed|seen)\b|von der Kamera", re.IGNORECASE)
SYSTEM_PHRASE = re.compile(r"vom System zugeordnet|assigned by the system", re.IGNORECASE)
OPERATOR_PHRASE = re.compile(r"laut Operator|nach Angabe des Operators|according to the operator", re.IGNORECASE)
REVERSE_PHRASE = re.compile(r"umgekehrten? Entnahmereihenfolge|reverse (?:of the )?removal order", re.IGNORECASE)
FIRST_THEN = re.compile(r"\b(?:zuerst|erst|first)\b.*\b(?:danach|dann|anschließend|then|afterwards)\b", re.IGNORECASE)
BEFORE = re.compile(r"\b(?:vor|before)\b", re.IGNORECASE)
AFTER = re.compile(r"\b(?:nach|after)\b", re.IGNORECASE)
DEPENDENCY = re.compile(r"\b(?:weil|da sie|because|darunter|darüber|underneath|on top of|abhängig|Abhängigkeit|depends?|"
                        r"requires?|erst wenn)\b|\b(?:muss|müssen|must)\b.*\b(?:vor|before|zuerst|first|nach|after)\b",
                        re.IGNORECASE)
SOURCE = re.compile(r"\b(?:Stelle|entnommen|kam|stammt|Mulde|Loch|Löcher|came from|taken from|place of|hole)\b",
                    re.IGNORECASE)
OWN_SOURCE = re.compile(r"\b(?:seine|ihre|its|their)\b\s+(?:\w+\s+)?(?:Stelle|Mulde|Loch|place|hole)", re.IGNORECASE)
WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
# Closed vocabulary of a free-text answer: function words and the words the checks above understand. A word
# outside it (and outside the package's operator labels) could carry an unchecked fact, so its sentence is
# rejected. Spatial and deictic words (unter, über, dort, hier, wo, neu) are left out on purpose.
VOCABULARY = """
der die das den dem des ein eine einer eines einem einen und oder aber also auch nur noch nicht kein keine keinen
ja nein in im ins an am aus auf bei mit von vom zu zum zur für zwischen nach vor ist sind wird werden wurde wurden
hat haben kann können soll sollen muss müssen es er sie ihr ihre ihren ihrer sein seine seinen seiner eigene eigenen
dies diese dieser diesen diesem dieses davon dazu welche welcher welches ob dass wenn als wie so zuerst erst dann
danach anschließend zurück wieder rein hinein ein eingesetzt einsetzen einzusetzen setze setzen setzt lege legen
gehört gehören kommt kommen liegt liegen gelegt abgelegt entnommen entnahme entnahmen entnahmestelle
entnahmereihenfolge umgekehrt umgekehrte umgekehrten reihenfolge teil teile teils teilen zelle zellen stelle stellen
gruppe beliebige beliebiges beliebigen löcher loch mulde identisch vermessen operator operators laut angabe system
zugeordnet zuordnung abhängigkeit abhängigkeiten bestätigt beobachtet sitzung paket sitzungspaket schritt phase
bezeichnung label region bereich anonym anonyme anonymen unbenannte unbekannt belegt information weiß ich direkt
per ausschluss aller alle anderen offenen regel gefolgert erschlossen jeweils als nächstes nächste du bitte kam
stammt austauschbar
the a an of from to in into is are was were be been goes go back before after first then by order reverse removal
part parts cell cells place its own group any one hole holes according operator system assigned not observed
confirmed unknown dependency between these this session no step label labelled called and or but also only that
which it they removed next you please came identical interchangeable measured
"""


@dataclass(frozen=True)
class Part:
    id: str
    basis: str
    label: str | None
    location: str | None
    record: str | None
    source: dict
    group: str | None
    step: int | None
    inferred_by: str | None

    @property
    def cell(self) -> str | None:
        return self.location[5:] if self.location and self.location.startswith("cell-") else None


@dataclass(frozen=True)
class Package:
    sha256: str
    parts: dict[str, Part]
    aliases: dict[str, tuple[str, ...]]  # citable id (part, storage record, group) -> part ids
    group_labels: dict[str, str | None]
    confirmed: tuple


@dataclass
class Verdict:
    accepted: bool
    speech: str
    claims: list[dict] = field(default_factory=list)  # {"claim": ..., "reasons": [...]}
    findings: list[str] = field(default_factory=list)  # problems not tied to one claim (schema, package, mapping)

    def as_json(self) -> dict:
        return {"verdict": "ACCEPT" if self.accepted else "REJECT", "speech": self.speech,
                "claims": self.claims, "findings": self.findings}


# ---------------------------------------------------------------- schema

def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    """Validate against the JSON Schema subset the live contract allows (plus `const`)."""
    if "type" in schema and not _has_type(value, schema["type"]):
        return [f"{path}: expected {schema['type']}"]
    errors = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
        errors.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", len(value)):
            errors.append(f"{path}: more than {schema['maxItems']} items")
        for n, item in enumerate(value):
            errors += schema_errors(item, schema.get("items", {}), f"{path}[{n}]")
    if isinstance(value, dict):
        errors += [f"{path}: missing {key!r}" for key in schema.get("required", []) if key not in value]
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: unexpected {key!r}" for key in value if key not in properties]
        errors += [e for key, sub in properties.items() if key in value for e in schema_errors(value[key], sub, f"{path}.{key}")]
    return errors


def _has_type(value, expected) -> bool:
    checks = {"string": lambda v: isinstance(v, str), "null": lambda v: v is None,
              "boolean": lambda v: isinstance(v, bool), "object": lambda v: isinstance(v, dict),
              "array": lambda v: isinstance(v, list),
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}
    return any(checks[t](value) for t in ([expected] if isinstance(expected, str) else expected))


def claims_schema() -> dict:
    # The basis_word enum (contract 0.3) constrains the model's output; here the word checks below classify a
    # wrong word (certainty, identity, later observation) instead of a bare schema error, so the enum is dropped.
    schema = json.loads(TASK_SCHEMA_FILE.read_text(encoding="utf-8"))["output"]
    schema["properties"]["claims"]["items"]["properties"]["basis_word"].pop("enum", None)
    return schema


# ---------------------------------------------------------------- package

def load_package(data: bytes, recorded_sha256: str | None) -> tuple[Package | None, str | None]:
    """The package if its bytes carry the recorded build hash and its layout is the current one, else the reason."""
    sha = hashlib.sha256(data).hexdigest()
    if not recorded_sha256:
        return None, "package: no build hash recorded; the package cannot be checked (F6)"
    if sha != recorded_sha256.strip().lower():
        return None, f"package: sha256 {sha[:12]}… differs from the hash recorded at build {recorded_sha256[:12]}… (F6)"
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, f"unsupported package schema: not UTF-8 JSON ({error})"
    problem = _layout_problem(raw)
    if problem:
        return None, f"unsupported package schema: {problem}"
    return _package(raw, sha), None


def _layout_problem(raw) -> str | None:
    if not isinstance(raw, dict) or raw.get("schema") != PACKAGE_SCHEMA:
        return f"schema is {raw.get('schema') if isinstance(raw, dict) else type(raw).__name__!r}, expected {PACKAGE_SCHEMA}"
    install, dependencies = raw.get("install_order"), raw.get("dependencies")
    if not isinstance(raw.get("parts"), list) or not isinstance(install, dict) or not isinstance(install.get("steps"), list):
        return "parts or install_order.steps missing"
    if not isinstance(dependencies, dict) or not isinstance(dependencies.get("confirmed"), list):
        return "dependencies.confirmed missing"
    for n, part in enumerate(raw["parts"], start=1):
        if not isinstance(part, dict) or not isinstance(part.get("id"), str):
            return f"part {n} has no id (older package layout)"
        if part.get("basis") not in BASES or not isinstance(part.get("source"), dict):
            return f"part {part['id']} has no basis or source"
        stored = part.get("stored_in")
        if stored is not None and not (isinstance(stored, dict) and isinstance(stored.get("location"), str)):
            return f"part {part['id']} has no stored_in.location"
    for n, step in enumerate(install["steps"], start=1):
        if not (isinstance(step, dict) and isinstance(step.get("step"), int) and isinstance(step.get("parts"), list)):
            return f"install step {n} has no parts list (older package layout)"
    return None


def _package(raw: dict, sha: str) -> Package:
    step_of = {pid: step["step"] for step in raw["install_order"]["steps"] for pid in step["parts"]}
    parts, aliases, group_labels = {}, {}, {}
    for item in raw["parts"]:
        stored = item.get("stored_in") or {}
        group = item.get("group") or {}
        part = Part(id=item["id"], basis=item["basis"], label=item.get("operator_label"), location=stored.get("location"),
                    record=stored.get("record"), source=item["source"], group=group.get("id"), step=step_of.get(item["id"]),
                    inferred_by=(stored.get("inference") or {}).get("inferred_by"))
        parts[part.id] = part
        aliases[part.id] = (part.id,)
        if part.record:
            aliases[part.record] = (part.id,)
        if part.group:
            aliases[part.group] = aliases.get(part.group, ()) + (part.id,)
            group_labels[part.group] = group.get("label")
    return Package(sha, parts, aliases, group_labels, tuple(raw["dependencies"]["confirmed"]))


# ---------------------------------------------------------------- claim verification

def verify_claim(claim: dict, package: Package) -> list[str]:
    """Reasons the package does not support the claim; empty means accepted. `unknown` is always accepted."""
    if claim["relation"] == "unknown":
        return []
    subjects = _resolve(claim["subject"], package)
    if not subjects:
        return [f"entity: {claim['subject']!r} is not an id in the package"]
    reasons = _word_problems(claim, subjects)
    check = {"stored_in": _check_stored_in, "removed_from": _check_removed_from, "install_before": _check_install_before,
             "basis": _check_basis, "dependency": _check_dependency}[claim["relation"]]
    return reasons + check(claim, subjects, package)


def _resolve(citable, package: Package) -> list[Part]:
    return [package.parts[i] for i in package.aliases.get(citable, ())] if isinstance(citable, str) else []


def _word_problems(claim: dict, subjects: list[Part]) -> list[str]:
    word, found = claim.get("basis_word"), []
    if word is not None and word not in BASIS_WORDS:
        found.append(_word_class(word) + f": {word!r} is not a basis word of GUIDE_RULES")
    automatic = [p.id for p in subjects if p.basis == "automatic"]
    for text in (word, claim.get("object")):
        if automatic and isinstance(text, str) and (COLOUR.search(text) or PART_NAME.search(text)):
            found.append(f"identity: {text!r} names automatic part(s) {', '.join(automatic)} (GUIDE_RULES 4)")
    return found


def _word_class(text: str) -> str:
    """Reason prefix for a phrase that is not an allowed basis word."""
    if LATER_OBSERVATION.search(text):
        return "later_observation"
    if CERTAINTY.search(text) or OBSERVED.search(text):
        return "certainty"
    if COLOUR.search(text) or PART_NAME.search(text):
        return "identity"
    return "basis"


def _basis_word_problems(claim: dict, subjects: list[Part]) -> list[str]:
    word, ids = claim.get("basis_word"), ", ".join(p.id for p in subjects)
    if word == SYSTEM_ASSIGNED and any(p.basis != "automatic" for p in subjects):
        return [f"basis: {ids} is operator_declared; '{SYSTEM_ASSIGNED}' inverts its basis (GUIDE_RULES 5a)"]
    if word == BY_OPERATOR and any(p.basis != "operator_declared" for p in subjects):
        return [f"basis: automatic {ids} attributed to the operator (GUIDE_RULES 5a)"]
    if word == GROUP_STATEMENT and (len({p.group for p in subjects}) != 1 or subjects[0].group is None):
        return [f"basis: {ids} is not one interchangeable group (GUIDE_RULES 5b)"]
    if word == REVERSE_ORDER and claim["relation"] != "install_before":
        return [f"basis: '{REVERSE_ORDER}' only supports an install order"]
    if word == NOT_OBSERVED:
        return [f"basis: '{NOT_OBSERVED}' does not support a positive {claim['relation']} claim"]
    return []


def _check_stored_in(claim, subjects, package) -> list[str]:
    locations = sorted({str(p.location) for p in subjects})
    if locations != [claim["object"]]:
        return [f"storage: {claim['subject']} is stored in {', '.join(locations)}, not {claim['object']}"]
    return _basis_word_problems(claim, subjects)


def _check_removed_from(claim, subjects, package) -> list[str]:
    target = claim["object"]
    if target is None:  # "its own place": the package's recorded source is what gets spoken
        return _basis_word_problems(claim, subjects)
    others = _resolve(target, package)
    if others:
        same = {p.id for p in subjects} == {p.id for p in others}
        one_group = len({p.group for p in subjects + others}) == 1 and subjects[0].group is not None
        if not (same or one_group):
            return [f"source: {claim['subject']} and {target} were removed from different places (GUIDE_RULES 1, 3)"]
    elif not all(re.search(rf"(?<![\w-]){re.escape(target)}(?![\w-])", _source_text(p)) for p in subjects):
        return [f"source: {target!r} is not a recorded source of {claim['subject']}"]
    return _basis_word_problems(claim, subjects)


def _source_text(part: Part) -> str:
    return " ".join(str(part.source.get(key) or "") for key in ("site", "described_as"))


def _check_install_before(claim, subjects, package) -> list[str]:
    others = _resolve(claim["object"], package)
    if not others:
        return [f"entity: {claim['object']!r} is not an id in the package"]
    steps = [p.step for p in subjects + others]
    if None in steps:
        return [f"order: {claim['subject']} or {claim['object']} has no install step in the package"]
    if max(p.step for p in subjects) >= min(p.step for p in others):
        return [f"order: install_order puts {claim['subject']} at step {'/'.join(str(p.step) for p in subjects)} "
                f"and {claim['object']} at step {'/'.join(str(p.step) for p in others)} (GUIDE_RULES 5)"]
    return _basis_word_problems(claim, subjects)


def _check_basis(claim, subjects, package) -> list[str]:
    if claim["object"] not in BASES:
        return [f"basis: object must be one of {BASES}, got {claim['object']!r}"]
    wrong = [p.id for p in subjects if p.basis != claim["object"]]
    if wrong:
        return [f"basis: {', '.join(wrong)} is not {claim['object']} (GUIDE_RULES 5a)"]
    return _basis_word_problems(claim, subjects)


def _check_dependency(claim, subjects, package) -> list[str]:
    # package.py never writes a confirmed dependency, so no entry format is defined; only an explicit
    # {"before": subject, "after": object} entry is recognised and everything else stays rejected.
    if {"before": claim["subject"], "after": claim["object"]} in package.confirmed:
        return _basis_word_problems(claim, subjects)
    return [f"dependency: no confirmed dependency {claim['subject']} before {claim['object']} in the package "
            "(GUIDE_RULES 3, 5)"]


# ---------------------------------------------------------------- free-text mapping

@dataclass
class Mapping:
    claims: list[dict]
    findings: list[str]


def map_answer(text: str, package: Package) -> Mapping:
    """Read claims out of a free-text answer with a fixed lexicon; what cannot be read becomes a finding."""
    mapping, questions = Mapping([], []), 0
    for sentence in (s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip())):
        if not sentence:
            continue
        if sentence.endswith("?"):  # GUIDE_RULES 3 clarifying question: never spoken, so it must carry no fact
            questions += 1
            if ID.search(sentence) or CELL.search(sentence) or _asserted(CERTAINTY, sentence):
                mapping.findings.append(f"unmapped: a question carries a fact: {sentence[:80]}")
            continue
        _map_sentence(sentence, package, mapping)
    if questions and not any(c["relation"] == "unknown" for c in mapping.claims):
        mapping.findings.append("unmapped: a clarifying question without a 'nicht beobachtet' statement")
    if questions > 1:
        mapping.findings.append("unmapped: more than one clarifying question (GUIDE_RULES 3)")
    return mapping


def _map_sentence(sentence: str, package: Package, mapping: Mapping) -> None:
    before = len(mapping.claims) + len(mapping.findings)
    cited = list(dict.fromkeys(ID.findall(sentence)))
    mapping.findings += [f"entity: {i} is not an id in the package" for i in cited if i not in package.aliases]
    cited = [i for i in cited if i in package.aliases]
    refs = _references(sentence, cited, package, mapping)
    uncertain = bool(UNCERTAIN.search(sentence))
    if uncertain:
        mapping.claims.append(_claim("unknown", refs[0] if refs else "session", None, NOT_OBSERVED))
    elif not cited:
        mapping.findings.append(f"uncited: a statement without a citation or 'nicht beobachtet' (GUIDE_RULES 2): {sentence[:80]}")
    if cited or not uncertain:
        _identity_findings(sentence, cited, package, mapping)
    for match in LATER_OBSERVATION.finditer(sentence):
        mapping.findings.append(f"later_observation: {match.group(0)!r} is a later image, not history (evidence rule)")
    for match in CERTAINTY.finditer(sentence):
        if _asserted_at(sentence, match):
            mapping.findings.append(f"certainty: {match.group(0)!r} is not on the GUIDE_RULES word list (4, 5a)")
    _basis_claims(sentence, cited, package, mapping)
    _relation_claims(sentence, refs, mapping)
    if len(mapping.claims) + len(mapping.findings) == before:
        mapping.findings.append(f"unmapped: no claim could be read from: {sentence[:80]}")
    outside = _outside_vocabulary(sentence, package)
    if outside:
        mapping.findings.append(f"unmapped: words the verifier cannot check: {', '.join(outside)}")


def _outside_vocabulary(sentence: str, package: Package) -> list[str]:
    """Words that are neither guide vocabulary nor part of an operator label in the package (fail-closed)."""
    labels = " ".join([p.label or "" for p in package.parts.values()] + [g or "" for g in package.group_labels.values()])
    known = VOCABULARY_STEMS | {_stem(w) for w in WORD.findall(labels)}
    text = SITE.sub(" ", ID.sub(" ", sentence))
    return list(dict.fromkeys(w for w in WORD.findall(text) if _stem(w) not in known))


def _references(sentence: str, cited: list[str], package: Package, mapping: Mapping) -> list[str]:
    """Cited ids in the order the sentence mentions their cells; adds a stored_in claim per mentioned cell."""
    refs = []
    for match in CELL.finditer(sentence):
        location = f"cell-{int(match.group(1))}"
        here = [i for i in cited if all(package.parts[p].location == location for p in package.aliases[i])]
        here = [i for i in here if not any(i != j and set(package.aliases[i]) < set(package.aliases[j]) for j in here)]
        if not here:
            if cited:
                mapping.claims.append(_claim("stored_in", cited[0], location, None))
            continue
        for ref in here[:1]:
            mapping.claims.append(_claim("stored_in", ref, location, None))
            if ref not in refs:
                refs.append(ref)
    for ref in cited:
        if not any(set(package.aliases[ref]) <= set(package.aliases[r]) for r in refs):
            refs.append(ref)
    return refs


def _identity_findings(sentence: str, cited: list[str], package: Package, mapping: Mapping) -> None:
    parts = [package.parts[p] for i in cited for p in package.aliases[i]]
    labels = " ".join((p.label or "") + " " + (package.group_labels.get(p.group) or "") for p in parts).lower()
    attributed = bool(OPERATOR_PHRASE.search(sentence))
    for match in [*COLOUR.finditer(sentence), *PART_NAME.finditer(sentence)]:
        word = match.group(0)
        automatic = sorted({p.id for p in parts if p.basis == "automatic"})
        if automatic:
            mapping.findings.append(f"identity: {word!r} names automatic part(s) {', '.join(automatic)} (GUIDE_RULES 4)")
        elif not parts:
            mapping.findings.append(f"identity: {word!r} names a part without a citation (GUIDE_RULES 4)")
        elif not (attributed and _stem(word) in labels.replace("ß", "ss")):
            mapping.findings.append(f"identity: {word!r} is not an operator label said as a label (GUIDE_RULES 4)")


def _stem(word: str) -> str:
    word = word.lower().replace("ß", "ss")
    return re.sub(r"(?:e|er|es|en|em|n|s)$", "", word) if len(word) > 4 else word


VOCABULARY_STEMS = frozenset(_stem(word) for word in VOCABULARY.split())


def _basis_claims(sentence: str, cited: list[str], package: Package, mapping: Mapping) -> None:
    group_statement = OPERATOR_PHRASE.search(sentence) and re.search(r"identisch|identical", sentence, re.IGNORECASE)
    phrases = [(SYSTEM_PHRASE, "automatic", SYSTEM_ASSIGNED),
               (OPERATOR_PHRASE, "operator_declared", GROUP_STATEMENT if group_statement else BY_OPERATOR)]
    for pattern, basis, word in phrases:
        if pattern.search(sentence):
            mapping.claims += [_claim("basis", ref, basis, word) for ref in cited if not _is_record(ref)]
    for match in OBSERVED.finditer(sentence):
        if _asserted_at(sentence, match):
            mapping.claims += [_claim("basis", ref, None, match.group(0)) for ref in cited if not _is_record(ref)]


def _is_record(citable: str) -> bool:
    return citable.startswith(("link-", "store-"))


def _relation_claims(sentence: str, refs: list[str], mapping: Mapping) -> None:
    ordered = _ordered_pair(sentence, refs)
    if ordered:
        word = REVERSE_ORDER if REVERSE_PHRASE.search(sentence) else None
        mapping.claims.append(_claim("install_before", *ordered, word))
    elif FIRST_THEN.search(sentence):
        mapping.findings.append(f"unmapped: an order without two cited parts: {sentence[:80]}")
    if any(DEPENDENCY.search(clause) and not NEGATION.search(clause) for clause in re.split(r"[;,]", sentence)):
        pair = ordered or (refs[0] if refs else "", refs[1] if len(refs) > 1 else None)
        mapping.claims.append(_claim("dependency", *pair, None))
    if SOURCE.search(sentence) and refs:
        sites = SITE.findall(sentence)
        if len(refs) > 1 and not OWN_SOURCE.search(sentence) and not ordered:
            mapping.claims.append(_claim("removed_from", refs[0], refs[1], None))
        elif sites:
            mapping.claims += [_claim("removed_from", refs[0], site, None) for site in sites]
        else:
            mapping.claims += [_claim("removed_from", ref, None, None) for ref in refs if not _is_record(ref)]


def _ordered_pair(sentence: str, refs: list[str]) -> tuple[str, str] | None:
    if len(refs) < 2:
        return None
    if FIRST_THEN.search(sentence):
        return refs[0], refs[1]
    cells = list(CELL.finditer(sentence))
    if len(cells) >= 2:
        between = sentence[cells[0].end():cells[1].start()]
        if BEFORE.search(between):
            return refs[0], refs[1]
        if AFTER.search(between):
            return refs[1], refs[0]
    return None


def _asserted(pattern: re.Pattern, sentence: str) -> bool:
    return any(_asserted_at(sentence, m) for m in pattern.finditer(sentence))


def _asserted_at(sentence: str, match: re.Match) -> bool:
    """No negation in the few words before the match."""
    return not NEGATION.search(sentence[max(0, match.start() - 25):match.start()])


def _claim(relation: str, subject: str, target, basis_word) -> dict:
    return {"relation": relation, "subject": subject, "object": target, "basis_word": basis_word}


# ---------------------------------------------------------------- verdict and speech

def verify(document, package: Package) -> Verdict:
    """Verify a model's anastylos-guide-claims@1 document."""
    errors = schema_errors(document, claims_schema())
    if errors:
        return Verdict(False, NOT_PROVEN, findings=[f"schema: {e}" for e in errors])
    return _verdict(document["claims"], [], package, document["insufficient_evidence"])


def verify_answer(text: str, package: Package) -> Verdict:
    """Verify a free-text answer by mapping it to claims first."""
    mapping = map_answer(text, package)
    return _verdict(mapping.claims, mapping.findings, package, False)


def check(package_bytes: bytes, recorded_sha256: str | None, *, document=None, answer: str | None = None) -> Verdict:
    """Entry point: load the package (hash + layout), then verify a claims document or a free-text answer."""
    package, problem = load_package(package_bytes, recorded_sha256)
    if problem:
        return Verdict(False, f"Das Sitzungspaket ist nicht prüfbar ({problem.split(':')[0]}); ich gebe keine Antwort.",
                       findings=[problem])
    return verify(document, package) if answer is None else verify_answer(answer, package)


def _verdict(claims: list[dict], findings: list[str], package: Package, insufficient: bool) -> Verdict:
    results = [{"claim": c, "reasons": verify_claim(c, package)} for c in claims]
    accepted = not findings and all(not r["reasons"] for r in results)
    return Verdict(accepted, render(claims, package, insufficient) if accepted else NOT_PROVEN, results, findings)


def render(claims: list[dict], package: Package, insufficient: bool = False) -> str:
    """German speech from verified claims only, by fixed templates; nothing of the model's wording is reused."""
    stored = {p.id for c in claims if c["relation"] == "stored_in" for p in _resolve(c["subject"], package)}
    sentences = []
    for claim in claims:
        if claim["relation"] == "unknown":
            continue
        if claim["relation"] == "basis" and {p.id for p in _resolve(claim["subject"], package)} <= stored:
            continue  # the storage sentence already says the basis
        sentence = _TEMPLATES[claim["relation"]](claim, package)
        if sentence not in sentences:
            sentences.append(sentence)
    if insufficient or not sentences:
        sentences.append(UNKNOWN_SPEECH)
    return " ".join(sentences)


def _name(citable: str, package: Package, *, by_cell: bool = True, dative: bool = False) -> str:
    """GUIDE_RULES 4: a part is named by its place, an operator label only as a label."""
    parts = _resolve(citable, package)
    if len(parts) > 1 or citable.startswith("grp-"):
        label = package.group_labels.get(parts[0].group)
        name = f"{'einem beliebigen' if dative else 'ein beliebiges'} Teil der Gruppe {parts[0].group}"
        return name + (f" (laut Operator „{label}“)" if label else "")
    part = parts[0]
    article = "dem" if dative else "das"
    name = f"{article} Teil aus Zelle {part.cell}" if part.cell and by_cell else f"{article} Teil {part.id}"
    return name + (f" (laut Operator „{part.label}“)" if part.label else "")


def _cites(*citables: str, package: Package) -> str:
    ids = []
    for citable in citables:
        group = citable.startswith("grp-")
        for part in _resolve(citable, package):
            ids += [part.id] if group else [i for i in (part.id, part.record) if i]
        if group:
            ids.append(citable)
    return "[" + ", ".join(dict.fromkeys(ids)) + "]"


def _basis_tail(citable: str, package: Package) -> str:
    part = _resolve(citable, package)[0]
    if part.basis == "operator_declared":
        return BY_OPERATOR
    rule = f" ({part.inferred_by})" if part.inferred_by else ""
    return f"{SYSTEM_ASSIGNED}{rule}, nicht beobachtet"


def _say_stored_in(claim, package) -> str:
    parts = _resolve(claim["subject"], package)
    part = parts[0]
    where = f"Zelle {part.cell}" if part.cell else f"„{part.location}“ (Ortsangabe des Operators)"
    if len(parts) > 1:
        label = package.group_labels.get(part.group)
        what = f"liegen die Teile der Gruppe {part.group}" + (f" (laut Operator „{label}“)" if label else "")
    else:
        what = f"liegt {_name(claim['subject'], package, by_cell=False)}"
    return f"In {where} {what}, {_basis_tail(claim['subject'], package)} {_cites(claim['subject'], package=package)}."


def _say_removed_from(claim, package) -> str:
    parts = _resolve(claim["subject"], package)
    if claim["subject"].startswith("grp-") or (claim["object"] and len({p.id for p in parts + _resolve(claim["object"], package)}) > 1):
        return (f"{_capital(_name(claim['subject'], package))} kommt in eine der Stellen der Gruppe, {GROUP_STATEMENT} "
                f"{_cites(parts[0].group, package=package)}.")
    part = parts[0]
    source = part.source
    if part.basis == "operator_declared":
        where = f"laut Operator „{source['described_as']}“" if source.get("described_as") else "vom Operator nicht angegeben, nicht beobachtet"
    elif source.get("site"):
        where = f"die Stelle {source['site']}"
    else:
        x, y = source.get("centroid_mm") or (None, None)
        where = f"eine unbenannte Stelle bei {x:.0f}/{y:.0f} mm" if x is not None else "eine unbenannte Stelle"
        if "region_" in str(source.get("note")):
            where += "; das ist eine Region, kein einzelner Ort"
    return f"{_capital(_name(claim['subject'], package))} gehört zurück an {where} {_cites(claim['subject'], package=package)}."


def _say_install_before(claim, package) -> str:
    first, then = _resolve(claim["subject"], package), _resolve(claim["object"], package)
    return (f"Nach der umgekehrten Entnahmereihenfolge kommt {_name(claim['subject'], package)} (Schritt {first[0].step}) "
            f"vor {_name(claim['object'], package, dative=True)} (Schritt {then[0].step}); eine Abhängigkeit ist nicht bestätigt "
            f"{_cites(claim['subject'], claim['object'], package=package)}.")


def _say_basis(claim, package) -> str:
    return f"Für {_name(claim['subject'], package)} gilt: {_basis_tail(claim['subject'], package)} {_cites(claim['subject'], package=package)}."


def _say_dependency(claim, package) -> str:
    return (f"Laut bestätigter Abhängigkeit kommt {_name(claim['subject'], package)} vor "
            f"{_name(claim['object'], package, dative=True)} "
            f"{_cites(claim['subject'], claim['object'], package=package)}.")


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


_TEMPLATES = {"stored_in": _say_stored_in, "removed_from": _say_removed_from, "install_before": _say_install_before,
              "basis": _say_basis, "dependency": _say_dependency}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", required=True, type=Path, help="session.json of the guide package")
    parser.add_argument("--package-sha256", required=True, help="SHA-256 recorded when the package was built")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--claims", type=Path, help="anastylos-guide-claims@1 document")
    source.add_argument("--answer", help="free-text answer to map and verify")
    args = parser.parse_args()
    document = None
    if args.claims:
        try:
            document = json.loads(args.claims.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            document = {"unreadable": str(error)}  # fails schema validation and is rejected
    verdict = check(args.session.read_bytes(), args.package_sha256, document=document, answer=args.answer)
    print(json.dumps(verdict.as_json(), indent=2, ensure_ascii=False))
    sys.exit(0 if verdict.accepted else 1)


if __name__ == "__main__":
    main()
