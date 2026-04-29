# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM

# JOSM Jython: convert selected ways into relations (one per way)
# - Creates a new relation per selected way
# - Copies all way tags onto the new relation
# - Adds the way as a member with role="outer"
# - Strips all tags from the way except source* tags

from javax.swing import JOptionPane
from org.openstreetmap.josm.data.osm import Relation, RelationMember
from org.openstreetmap.josm.command import AddCommand, ChangeCommand, ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data import UndoRedoHandler

def log(msg):
    JOptionPane.showMessageDialog(None, msg, "way-to-relation", JOptionPane.INFORMATION_MESSAGE)

def main():
    layer = MainApplication.getLayerManager().getEditLayer()
    if layer is None:
        log("No active edit layer.")
        return
    ds = layer.data

    selected_ways = [p for p in ds.getSelected() if p.getClass().getSimpleName() == "Way"]
    if not selected_ways:
        log("No ways selected.")
        return

    cmds = []
    created = 0
    skipped = []

    for way in selected_ways:
        way_tags = dict(way.getKeys())  # snapshot
        if not way_tags:
            skipped.append(way.getUniqueId())
            continue

        # 1) Build the new relation with the way's tags + the way as outer member
        new_rel = Relation()
        for k, v in way_tags.items():
            new_rel.put(k, v)
        new_rel.addMember(RelationMember("outer", way))
        cmds.append(AddCommand(ds, new_rel))

        # 2) Strip way tags except source*
        for k in list(way_tags.keys()):
            if k == "source" or k.startswith("source:"):
                continue
            cmds.append(ChangePropertyCommand([way], k, None))

        created += 1

    if not cmds:
        log("Nothing to do — selected ways had no tags.")
        return

    seq = SequenceCommand("Convert {0} way(s) to relations".format(created), cmds)
    UndoRedoHandler.getInstance().add(seq)

    msg = "Created {0} relation(s).".format(created)
    if skipped:
        msg += "\nSkipped {0} untagged way(s): {1}".format(
            len(skipped), ", ".join(str(x) for x in skipped[:10])
        )
        if len(skipped) > 10:
            msg += " ..."
    log(msg)

main()
