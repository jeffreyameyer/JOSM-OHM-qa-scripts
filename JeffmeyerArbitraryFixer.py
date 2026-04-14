# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# please only use this if you are OHM user:jeffmeyer
# or are looking to see how scripts work

"""
normalize_arbitrary_dates.py

Finds OSM primitives where start_date or end_date matches YYYY-02-27,
YYYY-02-28, or YYYY-02-29 AND the corresponding :source tag equals
"arbitrary", then normalizes them:

  - {prefix}           -> YYYY
  - {prefix}:edtf      -> YYYY/ (start) or /YYYY (end)
  - {prefix}:confidence -> low
  - {prefix}:source    -> "arbitrary, proximate to date of geometry source"
  - fixme              -> deleted

Works on the current data layer's selected objects, or all objects if
nothing is selected.
"""

import re
from java.util import ArrayList
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.data import UndoRedoHandler
from org.openstreetmap.josm.gui import MainApplication

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FEB_PATTERN = re.compile(r'^(\d{4})-02-(27|28|29)$')
SOURCE_TRIGGER = "arbitrary"
NEW_SOURCE = "arbitrary, proximate to date of geometry source"


def get_year(date_str):
    """Return the YYYY portion if date_str is a Feb 27/28/29 date, else None."""
    m = FEB_PATTERN.match(date_str.strip())
    return m.group(1) if m else None


def build_commands(obj, prefix, year, is_start):
    """
    Return a list of ChangePropertyCommand instances for one prefix
    (either 'start_date' or 'end_date').
    """
    cmds = []

    # Normalize the main date tag to YYYY
    cmds.append(ChangePropertyCommand([obj], prefix, year))

    # Set EDTF open-ended interval
    if is_start:
        cmds.append(ChangePropertyCommand([obj], prefix + ":edtf", year + "/"))
    else:
        cmds.append(ChangePropertyCommand([obj], prefix + ":edtf", "/" + year))

    # Mark confidence as low
    cmds.append(ChangePropertyCommand([obj], prefix + ":confidence", "low"))

    # Update the source tag
    cmds.append(ChangePropertyCommand([obj], prefix + ":source", NEW_SOURCE))

    return cmds


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

layer = MainApplication.getLayerManager().getEditLayer()
if layer is None:
    raise Exception("No edit layer found. Please open a data layer first.")

ds = layer.getDataSet()

# Use selection if anything is selected; otherwise process all primitives
selected = list(ds.getSelected())
candidates = selected if selected else list(ds.allPrimitives())

all_commands = []
changed_count = 0

for obj in candidates:
    if obj.isDeleted() or not obj.isTagged():
        continue

    obj_commands = []

    for prefix, is_start in [("start_date", True), ("end_date", False)]:
        date_val = obj.get(prefix)
        source_val = obj.get(prefix + ":source")

        if not date_val or not source_val:
            continue

        year = get_year(date_val)
        if year is None:
            continue

        if source_val.strip() != SOURCE_TRIGGER:
            continue

        # This prefix qualifies — build its commands
        obj_commands.extend(build_commands(obj, prefix, year, is_start))

    if obj_commands:
        # Delete any fixme tag on this object
        if obj.get("fixme") is not None:
            obj_commands.append(ChangePropertyCommand([obj], "fixme", None))

        all_commands.extend(obj_commands)
        changed_count += 1

if all_commands:
    seq = SequenceCommand(
        "Normalize arbitrary Feb dates (%d object(s))" % changed_count,
        all_commands
    )
    UndoRedoHandler.getInstance().add(seq)
    print("Done. Modified %d object(s). Use Ctrl+Z to undo." % changed_count)
else:
    print("No matching objects found.")
