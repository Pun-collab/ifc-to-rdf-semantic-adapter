"""
==========================================================
IFC STEP  →  BOT A-BOX (Turtle)
==========================================================

This file does exactly 4 conceptual steps:

1. IFC → BOT mappings
   - We define WHICH IFC entity/relationship types we care about.
   - We classify them into:
       * Zone classes   (Site / Building / Storey / Space)
       * Element class  (Wall, Slab, Door, etc.)
       * Relationship types (Aggregates, Containment, Adjacency, Intersection)

2. Scan the IFC STEP and choose only the chosen types
   - We read the IFC STEP text.
   - We detect lines of the form: #id = IFCTYPE(...);
   - We KEEP ONLY:
       * spatial entities we care about
       * element entities we care about
       * relationship entities we care about

3. Parse and get the string (no BOT semantics yet)
   - We turn each relevant line into a neutral Python object:
       * IfcEntity(id, type, name, global_id)
       * IfcRelationship(id, type, parent_id, child_ids)
   - At this point we still speak "IFC language", not "BOT language".

4. Convert the parsed structures into RDF triples
   - We map IFC types → BOT classes.
   - We map IFC relationships → BOT properties.
   - We emit a Turtle (.ttl) string containing ONLY instance data (A-box).
   - This TTL can be uploaded into Jena together with your T-box schema.
==========================================================
"""

import re
from collections import defaultdict
from typing import Dict, List, Tuple, Optional


# ==========================================================
# STEP 1 — IFC → BOT MAPPINGS (WHAT WE CARE ABOUT)
# ==========================================================

# 1.1 IFC spatial entities → BOT zone classes
#     (These will become individuals of bot:Site, bot:Building, etc.)
IFC_TO_BOT_ZONE: Dict[str, str] = {
    "IFCSITE":             "bot:Site",
    "IFCBUILDING":         "bot:Building",
    "IFCBUILDINGSTOREY":   "bot:Storey",
    "IFCSPACE":            "bot:Space",
    # You can add more spatial types if needed.
}

# 1.2 IFC element entities → bot:Element
#     (All of these will become individuals of bot:Element)
IFC_TO_BOT_ELEMENT: Dict[str, str] = {
    "IFCWALL":             "bot:Element",
    "IFCWALLSTANDARDCASE": "bot:Element",
    "IFCSLAB":             "bot:Element",
    "IFCBEAM":             "bot:Element",
    "IFCCOLUMN":           "bot:Element",
    "IFCDOOR":             "bot:Element",
    "IFCWINDOW":           "bot:Element",
    "IFCPLATE":            "bot:Element",
    "IFCFURNISHINGELEMENT": "bot:Element",
    "IFCSTAIR":            "bot:Element",
    "IFCSTAIRFLIGHT":      "bot:Element",
    "IFCFLOWTERMINAL":     "bot:Element",
    # Extend with more element types if your building uses them.
}

# 1.3 IFC relationship types → BOT property roles
#     Note: Some IFC relations are "multi-purpose"; we decide the exact BOT
#           property later, based on the types of parent and child entities.
IFC_REL_TO_BOT_PROP: Dict[str, str] = {
    # IFCRELAGGREGATES can mean:
    #   Zone → Zone       → bot:containsZone
    #   Element → Element → bot:hasSubElement
    "IFCRELAGGREGATES": "bot:containsZone_or_hasSubElement",

    # IFCRELCONTAINEDINSPATIALSTRUCTURE:
    #   Zone → {Elements} → bot:containsElement
    "IFCRELCONTAINEDINSPATIALSTRUCTURE": "bot:containsElement",

    # IFCRELCONNECTSELEMENTS:
    #   Element ↔ Element adjacency → bot:adjacentElement
    "IFCRELCONNECTSELEMENTS": "bot:adjacentElement",

    # IFCRELINTERFERESELEMENTS (optional):
    #   Element ↔ Element intersection → bot:intersectingElement
    "IFCRELINTERFERESELEMENTS": "bot:intersectingElement",
}

# All IFC types we care about (entities + relationships)
IFC_TYPES_OF_INTEREST = (
    set(IFC_TO_BOT_ZONE.keys())
    | set(IFC_TO_BOT_ELEMENT.keys())
    | set(IFC_REL_TO_BOT_PROP.keys())
    | {"IFCPROJECT"}  # often useful as root, though not mapped to BOT
)


# ==========================================================
# STEP 2 — SCAN IFC STEP AND SELECT ONLY RELEVANT LINES
# ==========================================================

def scan_ifc_and_extract_lines(ifc_text: str) -> List[Tuple[int, str, str]]:
    """
    Scan the IFC STEP text and extract ONLY the lines of the form:

        #<id> = <IFCTYPE>(<args>);

    where IFCTYPE is in IFC_TYPES_OF_INTEREST.

    Returns a list of tuples:
        (id, ifc_type, raw_args_string)

    This step is still "IFC world", no BOT semantics yet.
    """

    # Regex:
    #   group(1) = id
    #   group(2) = IFCTYPE
    #   group(3) = arguments inside parentheses (multi-line allowed)
    pattern = re.compile(
        r"#(\d+)\s*=\s*(\w+)\s*\((.*?)\);",
        re.IGNORECASE | re.DOTALL
    )

    results: List[Tuple[int, str, str]] = []

    for match in pattern.finditer(ifc_text):
        ent_id_str, ent_type_raw, args_str = match.groups()
        ent_id = int(ent_id_str)
        ent_type = ent_type_raw.upper()

        # Only keep IFC types we explicitly care about
        if ent_type in IFC_TYPES_OF_INTEREST:
            results.append((ent_id, ent_type, args_str))

    return results


# ==========================================================
# STEP 3 — PARSE SELECTED IFC LINES (NO BOT SEMANTICS YET)
# ==========================================================

class IfcEntity:
    """
    Neutral representation of an IFC ENTITY we care about.
    Examples:
        #30 = IFCBUILDING('BLDG-001','Main Building', ...);

    We only store:
        - id        : 30
        - type      : 'IFCBUILDING'
        - name      : 'Main Building'
        - global_id : 'BLDG-001'
    """

    def __init__(self, ent_id: int, ent_type: str,
                 name: str = "", global_id: str = ""):
        self.id = ent_id
        self.type = ent_type.upper()
        self.name = name
        self.global_id = global_id

    def __repr__(self) -> str:
        return f"IfcEntity(#{self.id}, {self.type}, name={self.name!r})"


class IfcRelationship:
    """
    Neutral representation of an IFC relationship we care about.
    Example:
        #100 = IFCRELAGGREGATES('X',$,$,$,#20,(#30,#40));

    We only store:
        - id        : 100
        - type      : 'IFCRELAGGREGATES'
        - parent_id : 20
        - child_ids : [30, 40]
    """

    def __init__(self, rel_id: int, rel_type: str,
                 parent_id: int, child_ids: List[int]):
        self.id = rel_id
        self.type = rel_type.upper()
        self.parent_id = parent_id
        self.child_ids = child_ids

    def __repr__(self) -> str:
        return (f"IfcRelationship(#{self.id}, {self.type}, "
                f"parent={self.parent_id}, children={self.child_ids})")


def parse_selected_ifc_lines(
    lines: List[Tuple[int, str, str]]
) -> Tuple[Dict[int, IfcEntity], List[IfcRelationship]]:
    """
    Parse the selected IFC lines (from Step 2) into:

        entities:      { ifc_id → IfcEntity }
        relationships: [ IfcRelationship ]

    We still stay in the IFC world here:
    - We understand which ids are entities, which are relations.
    - But we have not yet mapped anything to BOT classes or properties.
    """

    entities: Dict[int, IfcEntity] = {}
    relationships: List[IfcRelationship] = []

    for ent_id, ent_type, args_str in lines:
        # CASE A: IFC entities (zones, elements, project)
        if ent_type in IFC_TO_BOT_ZONE or ent_type in IFC_TO_BOT_ELEMENT or ent_type == "IFCPROJECT":
            # Very simple extraction: first two string attributes
            # are taken as (global_id, name).
            # Pattern: 'GLOBALID','NAME',...
            m_args = re.match(r"'([^']*)'\s*,\s*'([^']*)'", args_str)
            global_id = m_args.group(1) if m_args else ""
            name = m_args.group(2) if m_args else ""

            entities[ent_id] = IfcEntity(ent_id, ent_type, name, global_id)

        # CASE B: IFC relationships
        elif ent_type in IFC_REL_TO_BOT_PROP:
            # We just need the #numbers involved.
            nums = re.findall(r"#(\d+)", args_str)
            if len(nums) >= 2:
                parent_id = int(nums[0])
                child_ids = [int(n) for n in nums[1:]]
                relationships.append(
                    IfcRelationship(ent_id, ent_type, parent_id, child_ids)
                )

        # Other IFC types are not included in IFC_TYPES_OF_INTEREST,
        # so they never appear here.

    return entities, relationships


# ==========================================================
# STEP 4 — CONVERT PARSED IFC TO BOT TRIPLES
# ==========================================================

def classify_ifc_entity(ent: IfcEntity) -> Optional[str]:
    """
    Map an IfcEntity to a BOT class (CURIE) or None.

    Examples:
        IFCBUILDING       → "bot:Building"
        IFCBUILDINGSTOREY → "bot:Storey"
        IFCSPACE          → "bot:Space"
        IFCWALL           → "bot:Element"
    """
    if ent.type in IFC_TO_BOT_ZONE:
        return IFC_TO_BOT_ZONE[ent.type]
    if ent.type in IFC_TO_BOT_ELEMENT:
        return IFC_TO_BOT_ELEMENT[ent.type]
    return None


def ifc_to_bot_triples(
    entities: Dict[int, IfcEntity],
    relationships: List[IfcRelationship]
) -> List[Tuple[str, str, str]]:
    """
    Convert IFC entities + relationships into RDF triples using BOT.

    OUTPUT:
        triples: List of (subject, predicate, object)
                 where s, p, o are CURIEs or literals (quoted).
    """

    triples: List[Tuple[str, str, str]] = []

    # Small helpers by id
    def is_zone_id(ifc_id: int) -> bool:
        e = entities.get(ifc_id)
        return e is not None and e.type in IFC_TO_BOT_ZONE

    def is_element_id(ifc_id: int) -> bool:
        e = entities.get(ifc_id)
        return e is not None and e.type in IFC_TO_BOT_ELEMENT

    # 4.1 Create rdf:type + rdfs:label triples for each instance
    for ent_id, ent in entities.items():
        bot_class = classify_ifc_entity(ent)
        if bot_class is None:
            # Example: IFCPROJECT => not mapped to BOT in this adapter
            continue

        subj = f"ex:inst_{ent_id}"

        # type triple
        triples.append((subj, "rdf:type", bot_class))

        # optional label triple from IFC name
        if ent.name:
            safe_name = ent.name.replace('"', '\\"')
            triples.append((subj, "rdfs:label", f"\"{safe_name}\""))

    # 4.2 Convert relationships to BOT object properties
    for rel in relationships:
        parent_id = rel.parent_id
        child_ids = rel.child_ids

        parent_curie = f"ex:inst_{parent_id}"
        children_curie = [f"ex:inst_{cid}" for cid in child_ids]

        # IFCRELAGGREGATES → containsZone or hasSubElement
        if rel.type == "IFCRELAGGREGATES":
            for cid, child_curie in zip(child_ids, children_curie):
                if is_zone_id(parent_id) and is_zone_id(cid):
                    triples.append((parent_curie, "bot:containsZone", child_curie))
                elif is_element_id(parent_id) and is_element_id(cid):
                    triples.append((parent_curie, "bot:hasSubElement", child_curie))
                # Mixed cases (zone-element or element-zone) are ignored here.

        # IFCRELCONTAINEDINSPATIALSTRUCTURE → containsElement
        elif rel.type == "IFCRELCONTAINEDINSPATIALSTRUCTURE":
            for cid, child_curie in zip(child_ids, children_curie):
                if is_zone_id(parent_id) and is_element_id(cid):
                    triples.append((parent_curie, "bot:containsElement", child_curie))

        # IFCRELCONNECTSELEMENTS → adjacentElement (symmetric)
        elif rel.type == "IFCRELCONNECTSELEMENTS":
            if len(child_ids) == 2:
                a_id, b_id = child_ids
                if is_element_id(a_id) and is_element_id(b_id):
                    a, b = children_curie
                    triples.append((a, "bot:adjacentElement", b))
                    triples.append((b, "bot:adjacentElement", a))

        # IFCRELINTERFERESELEMENTS → intersectingElement (symmetric)
        elif rel.type == "IFCRELINTERFERESELEMENTS":
            if len(child_ids) == 2:
                a_id, b_id = child_ids
                if is_element_id(a_id) and is_element_id(b_id):
                    a, b = children_curie
                    triples.append((a, "bot:intersectingElement", b))
                    triples.append((b, "bot:intersectingElement", a))

    return triples


def triples_to_ttl(triples: List[Tuple[str, str, str]]) -> str:
    """
    Render a list of (s, p, o) triples as Turtle A-BOX text.

    NOTE:
    - We include only instance data here.
    - Your T-BOX (schema) ontology is defined and loaded separately.
    """

    lines: List[str] = []

    # Prefixes must match what you use in Jena and in your T-box file.
    lines.append('@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .')
    lines.append('@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .')
    lines.append('@prefix bot:  <https://w3id.org/bot#> .')
    lines.append('@prefix ex:   <http://example.com/instances#> .')
    lines.append('')

    # Group triples by subject for nicer formatting
    by_subject: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for s, p, o in triples:
        by_subject[s].append((p, o))

    for subj, props in by_subject.items():
        lines.append(subj)
        for i, (p, o) in enumerate(props):
            sep = " ;" if i < len(props) - 1 else " ."
            lines.append(f"    {p} {o}{sep}")
        lines.append("")

    return "\n".join(lines)


# OPTIONAL: Small helper to run whole pipeline on a file.
# You can keep or delete this to match your thesis constraints.
def convert_ifc_file_to_ttl(ifc_path: str, ttl_path: str) -> None:
    """
    Convenience wrapper:
      IFC file  →  BOT A-box TTL file

    Usage (from terminal):
      >>> convert_ifc_file_to_ttl("mybuilding.ifc", "mybuilding.ttl")
    """
    with open(ifc_path, "r", encoding="utf-8", errors="ignore") as f:
        ifc_text = f.read()

    # Step 2: scan & select
    selected = scan_ifc_and_extract_lines(ifc_text)

    # Step 3: parse into neutral IFC objects
    entities, relationships = parse_selected_ifc_lines(selected)

    # Step 4: map IFC → BOT triples
    triples = ifc_to_bot_triples(entities, relationships)

    # Render TTL
    ttl_output = triples_to_ttl(triples)

    # Save TTL to disk
    with open(ttl_path, "w", encoding="utf-8") as f:
        f.write(ttl_output)


if __name__ == "__main__":
    # When you run:  python adapter.py
    # This will read example.ifc and create output.ttl in the same folder.
    convert_ifc_file_to_ttl("example.ifc", "output.ttl")

