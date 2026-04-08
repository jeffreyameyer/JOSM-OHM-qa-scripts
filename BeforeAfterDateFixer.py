# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# All users should verify proper function on their own before uploading changes to OHM

import re
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.data import UndoRedoHandler
from java.util import ArrayList

layer = MainApplication.getLayerManager().getEditLayer()
if not layer:
    raise Exception("No active edit layer found.")

ds = layer.getDataSet()

BEFORE_RE = re.compile(r'^before\s+(\S+)$', re.IGNORECASE)
AFTER_RE  = re.compile(r'^after\s+(\S+)$',  re.IGNORECASE)

DATE_KEYS = ['start_date', 'end_date']

commands = ArrayList()

for primitive in ds.allPrimitives():
    if primitive.isDeleted():
        continue

    for key in DATE_KEYS:
        val = primitive.get(key)
        if not val:
            continue

        m_before = BEFORE_RE.match(val)
        m_after  = AFTER_RE.match(val)

        if m_before:
            year = m_before.group(1)
            edtf_val = '/' + year
        elif m_after:
            year = m_after.group(1)
            edtf_val = year + '/'
        else:
            continue

        edtf_key = key + ':edtf'
        fixme_key = key + ':edtf_fixme'

        commands.add(ChangePropertyCommand(primitive, key, year))

        if primitive.get(edtf_key):
            commands.add(ChangePropertyCommand(primitive, fixme_key, edtf_val))
        else:
            commands.add(ChangePropertyCommand(primitive, edtf_key, edtf_val))

if commands.isEmpty():
    print("No matching objects found.")
else:
    seq = SequenceCommand("Fix before/after dates to EDTF", commands)
    UndoRedoHandler.getInstance().add(seq)
    print("Done. {} change(s) applied.".format(commands.size()))
