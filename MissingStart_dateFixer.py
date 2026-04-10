# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script takes objects with end_date, but no start_date
# and sets start_date=end_date and start_date:edtf=\end_date

from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance
from java.util import ArrayList

layer = MainApplication.getLayerManager().getEditLayer()
ds = layer.getDataSet()

commands = ArrayList()

for obj in ds.getSelected():
    if obj.isDeleted() or obj.isIncomplete():
        continue

    end_date = obj.get("end_date")
    start_date = obj.get("start_date")

    if end_date and not start_date:
        commands.add(ChangePropertyCommand([obj], "start_date", end_date))
        commands.add(ChangePropertyCommand([obj], "start_date:edtf", "/" + end_date))

if not commands.isEmpty():
    getInstance().add(SequenceCommand("Set start_date from end_date", commands))
    print("Updated {} objects".format(commands.size() / 2))
else:
    print("No selected objects found needing update")
