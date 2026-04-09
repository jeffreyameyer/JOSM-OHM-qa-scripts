# Written by Claude.ai
# Prompted by Jeff Meyer
# Please test this script on a limited set of chronologies before uploading to OHM

from org.openstreetmap.josm.data.osm import Relation
from org.openstreetmap.josm.gui import MainApplication
from javax.swing import JOptionPane

def parse_date(date_str):
    if not date_str:
        return None
    parts = date_str.strip().split('-')
    try:
        year  = int(parts[0]) if len(parts) > 0 else 0
        month = int(parts[1]) if len(parts) > 1 else 1
        day   = int(parts[2]) if len(parts) > 2 else 1
        return (year, month, day)
    except (ValueError, IndexError):
        return None

def format_date(original_str, new_tuple):
    if not original_str or not new_tuple:
        return None
    parts = original_str.strip().split('-')
    precision = len(parts)
    if precision == 1:
        return str(new_tuple[0])
    elif precision == 2:
        return '{}-{:02d}'.format(new_tuple[0], new_tuple[1])
    else:
        return '{}-{:02d}-{:02d}'.format(new_tuple[0], new_tuple[1], new_tuple[2])

def process_chronology_relation(relation):
    if relation.get('type') != 'chronology':
        return

    min_start         = None
    min_start_raw     = None
    max_start         = None
    max_start_raw     = None
    max_start_end_raw = None
    max_end           = None
    max_end_raw       = None

    members = [m.getMember() for m in relation.getMembers()
               if isinstance(m.getMember(), Relation)]

    for child in members:
        start_raw = child.get('start_date')
        end_raw   = child.get('end_date')

        if start_raw:
            parsed = parse_date(start_raw)
            if parsed is not None:
                if min_start is None or parsed < min_start:
                    min_start     = parsed
                    min_start_raw = start_raw
                if max_start is None or parsed > max_start:
                    max_start         = parsed
                    max_start_raw     = start_raw
                    max_start_end_raw = end_raw

        if end_raw:
            parsed = parse_date(end_raw)
            if parsed is not None:
                if max_end is None or parsed > max_end:
                    max_end     = parsed
                    max_end_raw = end_raw

    # --- start_date ---
    if min_start is not None:
        relation.put('start_date', format_date(min_start_raw, min_start))

    # --- end_date ---
    if max_start is not None and max_start_end_raw is None:
        if relation.get('end_date') is not None:
            relation.remove('end_date')
    elif max_end is not None:
        relation.put('end_date', format_date(max_end_raw, max_end))


# ── Main ──────────────────────────────────────────────────────────────────────

layer = MainApplication.getLayerManager().getEditLayer()
if layer is None:
    JOptionPane.showMessageDialog(None, "ERROR: No active edit layer.")
else:
    ds = layer.getDataSet()
    selected = list(ds.getSelectedRelations())
    chronology_relations = [r for r in selected if r.get('type') == 'chronology']

    if not chronology_relations:
        JOptionPane.showMessageDialog(None, "No type=chronology relations in the current selection.")
    else:
        for rel in chronology_relations:
            process_chronology_relation(rel)
