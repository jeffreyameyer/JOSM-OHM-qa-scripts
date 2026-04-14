# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script finds end_date or start_date tags in the format [DD.]MM.YYYY
# and changes them to YYYY-MM or YYYY-MM-DD,

"""
normalize_dot_dates.py

Converts start_date and end_date tags from DD.MM.YYYY format to YYYY-MM-DD
for all selected objects in JOSM/OHM.

Usage: Run via JOSM Scripting plugin (Tools > Run Script)
"""

import re
from java.util import ArrayList
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data import UndoRedoHandler

DATE_KEYS = ["start_date", "end_date"]
DOT_DATE_FULL_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
DOT_DATE_MY_RE   = re.compile(r"^(\d{1,2})\.(\d{4})$")

def convert_dot_date(value):
    """Convert DD.MM.YYYY -> YYYY-MM-DD or MM.YYYY -> YYYY-MM. Returns None if no match."""
    v = value.strip()
    m = DOT_DATE_FULL_RE.match(v)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        return "{}-{}-{}".format(yyyy, mm.zfill(2), dd.zfill(2))
    m = DOT_DATE_MY_RE.match(v)
    if m:
        mm, yyyy = m.group(1), m.group(2)
        return "{}-{}".format(yyyy, mm.zfill(2))
    return None

def main():
    ds = MainApplication.getLayerManager().getEditDataSet()
    if ds is None:
        print("ERROR: No active data set. Please open a layer first.")
        return

    selected = list(ds.getSelected())
    if not selected:
        print("ERROR: No objects selected. Please select objects first.")
        return

    commands = ArrayList()
    changes = []

    for obj in selected:
        for key in DATE_KEYS:
            value = obj.get(key)
            if value is None:
                continue
            new_value = convert_dot_date(value)
            if new_value is not None:
                commands.add(ChangePropertyCommand([obj], key, new_value))
                changes.append((str(obj), key, value, new_value))

    if commands.isEmpty():
        print("No DD.MM.YYYY dates found in selected objects.")
        return

    UndoRedoHandler.getInstance().add(
        SequenceCommand("Normalize dot-format dates", commands)
    )

    print("Updated {} tag(s):".format(len(changes)))
    for obj_str, key, old, new in changes:
        print("  {} | {} : {} -> {}".format(obj_str, key, old, new))

main()
