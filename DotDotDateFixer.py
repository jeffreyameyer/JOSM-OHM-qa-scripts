# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script finds end_date or start_date tags in the format XXXX..YYYY
# and changes them to end_date=max(XXXX,YYYY) or start_date=min(XXXX,YYYY),
# sets either end_date:edtf or start_date:edtf to XXXX/YYYY,
# and stores the original key value in end_date:raw or start_date:raw

import re
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance
from java.util import ArrayList

# Matches YYYY..ZZZZ where YYYY and ZZZZ are 1-4 digit year values
RANGE_RE = re.compile(r'^(\d{1,4})\.\.(\d{1,4})$')

def pad_year(y):
    """Format an integer year as a zero-padded 4-digit string."""
    return "{:04d}".format(y)

layer = MainApplication.getLayerManager().getEditLayer()
if layer is None:
    raise Exception("No active edit layer.")

ds = layer.getDataSet()
selected = list(ds.getSelected())

if not selected:
    print("No objects selected.")
else:
    all_commands = ArrayList()
    processed = 0

    for obj in selected:
        if obj.isDeleted() or obj.isIncomplete():
            continue

        for date_key in ("start_date", "end_date"):
            original = obj.get(date_key)
            if original is None:
                continue

            m = RANGE_RE.match(original)
            if not m:
                continue

            year_a = int(m.group(1))
            year_b = int(m.group(2))

            if date_key == "start_date":
                new_date = pad_year(min(year_a, year_b))
            else:  # end_date
                new_date = pad_year(max(year_a, year_b))

            edtf_value = "{}/{}".format(pad_year(year_a), pad_year(year_b))
            raw_key  = date_key + ":raw"
            edtf_key = date_key + ":edtf"

            # Store original in :raw
            all_commands.add(ChangePropertyCommand([obj], raw_key, original))
            # Write resolved single year back to the date key
            all_commands.add(ChangePropertyCommand([obj], date_key, new_date))
            # Write EDTF interval
            all_commands.add(ChangePropertyCommand([obj], edtf_key, edtf_value))

            print(u"  [{}] {} '{}' -> '{}', :edtf='{}', :raw='{}'".format(
                obj, date_key, original, new_date, edtf_value, original))
            processed += 1

    if all_commands.size() > 0:
        getInstance().add(SequenceCommand("Fix YYYY..ZZZZ date ranges", all_commands))
        print("Done. Processed {} date tag(s).".format(processed))
    else:
        print("No YYYY..ZZZZ date range tags found in selection.")
