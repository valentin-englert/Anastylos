"""English rendering of verified guide claims: the trusted renderer of the product (English-first).

Input is only what ``tools/guide/claims.py`` has accepted against the exact session package; nothing of a
model's wording is reused. The claims keep their internal values (relations, package ids and the GUIDE_RULES
``basis_word`` values, which stay German as contract values); this module translates presentation only.
Operator labels and place descriptions are quoted verbatim as the operator gave them: they are declarations,
never upgraded to observed facts (GUIDE_RULES 4, 5a). Automatic assignments say how they were concluded.
"""

from __future__ import annotations

# Presentation of the contract's basis words (internal values from tools/guide/claims.py, unchanged).
BASIS_TEXT = {
    "vom System zugeordnet": "assigned by the system",
    "laut Operator": "according to the operator",
    "laut Operator identisch, nicht vermessen": "identical according to the operator, not measured",
    "umgekehrte Entnahmereihenfolge": "reverse of the removal order",
    "nicht beobachtet": "not observed",
}
NOT_PROVEN = "The session package does not support this answer, so I will not give it."
UNKNOWN = "This was not observed in this session."
UNCHECKABLE = "The session package cannot be checked ({reason}); I will not answer from it."


def q(text: str) -> str:
    """Quote operator words verbatim."""
    return f"“{text}”"


def resolve(citable, package) -> list:
    return [package.parts[i] for i in package.aliases.get(citable, ())] if isinstance(citable, str) else []


def name(citable: str, package, *, by_cell: bool = True) -> str:
    """A part is named by its place or id; an operator label is only ever a label (GUIDE_RULES 4)."""
    parts = resolve(citable, package)
    if len(parts) > 1 or citable.startswith("grp-"):
        label = package.group_labels.get(parts[0].group)
        return f"any part of group {parts[0].group}" + (f" (operator label {q(label)})" if label else "")
    part = parts[0]
    text = f"the part from cell {part.cell}" if part.cell and by_cell else f"the part {part.id}"
    return text + (f" (operator label {q(part.label)})" if part.label else "")


def cites(*citables: str, package) -> str:
    ids = []
    for citable in citables:
        group = citable.startswith("grp-")
        for part in resolve(citable, package):
            ids += [part.id] if group else [i for i in (part.id, part.record) if i]
        if group:
            ids.append(citable)
    return "[" + ", ".join(dict.fromkeys(ids)) + "]"


def basis_tail(citable: str, package) -> str:
    part = resolve(citable, package)[0]
    if part.basis == "operator_declared":
        return BASIS_TEXT["laut Operator"]
    rule = f" ({part.inferred_by.replace('_', ' ')})" if part.inferred_by else ""
    return f"{BASIS_TEXT['vom System zugeordnet']}{rule}, not observed"


def where(part) -> str:
    return f"in cell {part.cell}" if part.cell else f"at {q(part.location)} (the operator's description of the place)"


def stored_in(claim, package) -> str:
    parts = resolve(claim["subject"], package)
    part = parts[0]
    if len(parts) > 1:
        label = package.group_labels.get(part.group)
        what = f"The parts of group {part.group}" + (f" (operator label {q(label)})" if label else "") + " are"
    else:
        what = f"{cap(name(claim['subject'], package, by_cell=False))} is"
    return f"{what} {where(part)}, {basis_tail(claim['subject'], package)} {cites(claim['subject'], package=package)}."


def removed_from(claim, package) -> str:
    parts = resolve(claim["subject"], package)
    if claim["subject"].startswith("grp-") or (claim["object"] and len({p.id for p in parts + resolve(claim["object"], package)}) > 1):
        return (f"{cap(name(claim['subject'], package))} goes into one of the group's places, "
                f"{BASIS_TEXT['laut Operator identisch, nicht vermessen']}; which part goes into which place was not "
                f"recorded {cites(parts[0].group, package=package)}.")
    part = parts[0]
    source = part.source
    if part.basis == "operator_declared":
        to = (f"{q(source['described_as'])}, according to the operator" if source.get("described_as")
              else "a place the operator did not state; not observed")
    elif source.get("site"):
        to = f"site {source['site']}"
    else:
        x, y = source.get("centroid_mm") or (None, None)
        to = f"an unnamed spot at {x:.0f}/{y:.0f} mm on the sheet" if x is not None else "an unnamed spot"
        if "region_" in str(source.get("note")):
            to += "; this is a region, not a single point"
    return f"{cap(name(claim['subject'], package))} goes back to {to} {cites(claim['subject'], package=package)}."


def install_before(claim, package) -> str:
    first, then = resolve(claim["subject"], package), resolve(claim["object"], package)
    return (f"By the {BASIS_TEXT['umgekehrte Entnahmereihenfolge']}, {name(claim['subject'], package)} (step {first[0].step}) "
            f"comes before {name(claim['object'], package)} (step {then[0].step}); no dependency is confirmed "
            f"{cites(claim['subject'], claim['object'], package=package)}.")


def basis(claim, package) -> str:
    return f"For {name(claim['subject'], package)}: {basis_tail(claim['subject'], package)} {cites(claim['subject'], package=package)}."


def dependency(claim, package) -> str:
    return (f"By a confirmed dependency, {name(claim['subject'], package)} comes before {name(claim['object'], package)} "
            f"{cites(claim['subject'], claim['object'], package=package)}.")


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


TEMPLATES = {"stored_in": stored_in, "removed_from": removed_from, "install_before": install_before,
             "basis": basis, "dependency": dependency}


def render(claims: list[dict], package, insufficient: bool = False) -> str:
    """English text from accepted claims only (same sentence selection as claims.render)."""
    stored = {p.id for c in claims if c["relation"] == "stored_in" for p in resolve(c["subject"], package)}
    sentences = []
    for claim in claims:
        if claim["relation"] == "unknown":
            continue
        if claim["relation"] == "basis" and {p.id for p in resolve(claim["subject"], package)} <= stored:
            continue  # the storage sentence already says the basis
        sentence = TEMPLATES[claim["relation"]](claim, package)
        if sentence not in sentences:
            sentences.append(sentence)
    if insufficient or not sentences:
        sentences.append(UNKNOWN)
    return " ".join(sentences)
