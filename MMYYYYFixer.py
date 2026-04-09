# Written by Claude
# Prompted by Jeff Meyer
# Please always verify data before uploading data to OHM
# This script is only for MM-YYYY format changes, as
# XX-XX-YYYY dates can be indeterminate and impossible to change

import re
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance

layer = MainApplication.getLayerManager().getEditLayer()
ds = layer.getDataSet()

commands = []
pattern = re.compile(r'^(\d{2})-(\d{4})$')

def fix_date(value):
    if value is None:
        return None
    m = pattern.match(value)
    if m:
        return u"{}-{}".format(m.group(2), m.group(1))
    return None

for primitive in list(ds.getSelected()):
    for tag_key in ("start_date", "end_date"):
        val = primitive.get(tag_key)
        new_val = fix_date(val)
        if new_val:
            print(u"{} [{}]: {} -> {}".format(primitive, tag_key, val, new_val))
            commands.append(ChangePropertyCommand(primitive, tag_key, new_val))

if commands:
    getInstance().add(SequenceCommand("Fix MM-YYYY date format to YYYY-MM", commands))
    print("Done: {} tags updated".format(len(commands)))
else:
    print("No matching date tags found")
