# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script finds end_date or start_date tags in the format ~YYYY
# and changes them to end_date or start_date=YYYY,
# sets either end_date:edtf or start_date:edtf to YYYY~
# and stores the original key value in end_date:raw or start_date:raw

import re
from org.openstreetmap.josm.data.osm import OsmPrimitive
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance
from java.util import ArrayList

# Matches ~Y, ~YY, ~YYY, ~YYYY, ~YYYY-MM, ~YYYY-MM-DD
TILDE_DATE = re.compile(r'^~(\d{1,4})(-\d{2})?(-\d{2})?$')

DATE_KEYS = ['start_date', 'end_date']

def normalize_tilde_date(val):
    """
    Parse a ~YYYY[-MM[-DD]] value. Returns (clean, raw, edtf) or None if no match.
    - clean: zero-padded date without tilde, e.g. "0753-04-21"
    - raw:   original tilde notation with zero-padded year, e.g. "~0753-04-21"
    - edtf:  EDTF approximate form, e.g. "0753-04-21~"
    """
    m = TILDE_DATE.match(val.strip())
    if not m:
        return None

    year  = m.group(1).zfill(4)   # pad to 4 digits
    month = m.group(2) or ''       # already "-MM" or empty
    day   = m.group(3) or ''       # already "-DD" or empty

    date_str = year + month + day  # e.g. "0753-04-21"
    return date_str, '~' + date_str, date_str + '~'

def process_objects(objs):
    commands = ArrayList()

    for obj in objs:
        for base_key in DATE_KEYS:
            val = obj.get(base_key)
            if val is None:
                continue

            result = normalize_tilde_date(val)
            if result is None:
                continue

            clean, raw, edtf = result

            # base key: strip tilde, zero-pad year
            commands.add(ChangePropertyCommand([obj], base_key, clean))
            # :raw key: tilde-prefixed, zero-padded year
            commands.add(ChangePropertyCommand([obj], base_key + ':raw', raw))
            # :edtf key: EDTF approximate suffix (~)
            commands.add(ChangePropertyCommand([obj], base_key + ':edtf', edtf))

            print("  {} {}: {} -> {}, :raw={}, :edtf={}".format(
                obj, base_key, val.strip(), clean, raw, edtf))

    if commands.size() == 0:
        print("No ~date values found in selection.")
        return

    seq = SequenceCommand("Fix ~date tags", commands)
    getInstance().add(seq)
    print("Done. {} change(s) applied.".format(commands.size()))

from org.openstreetmap.josm.gui import MainApplication
selected = list(MainApplication.getLayerManager().getEditDataSet().getSelected())

if not selected:
    print("No objects selected.")
else:
    print("Checking {} object(s)...".format(len(selected)))
    process_objects(selected)
