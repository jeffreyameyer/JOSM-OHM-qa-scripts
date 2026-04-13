# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script finds relations that fail the multipolygon test
# with role-related errors and fixes them
# please confirm that this script actually fixes the errors you are seeing
# as the script may be narrowly defined

from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.validation.tests import MultipolygonTest
from org.openstreetmap.josm.command import ChangeCommand, SequenceCommand
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance
from org.openstreetmap.josm.data.osm import RelationMember, Relation
import javax.swing.JOptionPane as JOptionPane
import javax.swing.JScrollPane as JScrollPane
import javax.swing.JTextArea as JTextArea
from java.util import ArrayList

layer = MainApplication.getLayerManager().getEditLayer()
ds = layer.data

test = MultipolygonTest()
test.initialize()
test.startTest(None)

for rel in ds.relations:
    if rel.isUsable():
        test.visit(rel)

test.endTest()

# Map of relation id -> {messages: set, ways: set}
failed = {}
for error in test.getErrors():
    if 'role' not in error.getMessage().lower():
        continue
    primitives = list(error.getPrimitives())
    rel_ids = [p.id for p in primitives if p.getType().name() == 'RELATION']
    way_ids = [p.id for p in primitives if p.getType().name() == 'WAY']
    for rid in rel_ids:
        if rid not in failed:
            failed[rid] = {'messages': set(), 'ways': set()}
        failed[rid]['messages'].add(error.getMessage())
        failed[rid]['ways'].update(way_ids)

# Pre-compute proposed changes
rel_lookup = {r.id: r for r in ds.relations}
planned = {}  # rid -> list of (way_id, old_role, new_role)

for rid, info in failed.items():
    rel = rel_lookup.get(rid)
    if rel is None or not rel.isUsable():
        continue
    target_way_ids = info['ways']
    changes = []
    for member in rel.members:
        if (member.isWay()
                and member.member.id in target_way_ids
                and member.role == 'outer'):
            changes.append((member.member.id, 'outer', 'inner'))
    if changes:
        planned[rid] = changes

# Build display
lines = []
for rid in sorted(planned.keys()):
    messages = "; ".join(sorted(failed[rid]['messages']))
    lines.append("Relation {} | {}".format(rid, messages))
    for way_id, old_role, new_role in sorted(planned[rid]):
        lines.append("  Way {}: '{}' -> '{}'".format(way_id, old_role, new_role))
    lines.append("")

if not planned:
    lines.append("No 'outer' members found to change in any failing relation.")

lines.append("{} relations to update, {} way role changes total".format(
    len(planned),
    sum(len(v) for v in planned.values())
))

msg = "\n".join(lines)

text_area = JTextArea(msg, 30, 80)
text_area.setLineWrap(True)
text_area.setWrapStyleWord(True)
text_area.setEditable(False)
scroll = JScrollPane(text_area)

confirm = JOptionPane.showConfirmDialog(
    None, scroll,
    "Proposed outer→inner fixes — Apply?",
    JOptionPane.OK_CANCEL_OPTION,
    JOptionPane.INFORMATION_MESSAGE
)

if confirm != JOptionPane.OK_OPTION:
    JOptionPane.showMessageDialog(None, "No changes made.", "Cancelled", JOptionPane.INFORMATION_MESSAGE)
else:
    commands = ArrayList()

    for rid, changes in planned.items():
        rel = rel_lookup.get(rid)
        if rel is None or not rel.isUsable():
            continue

        target_way_ids = {way_id for way_id, _, _ in changes}
        new_members = []
        for member in rel.members:
            if (member.isWay()
                    and member.member.id in target_way_ids
                    and member.role == 'outer'):
                new_members.append(RelationMember('inner', member.member))
            else:
                new_members.append(member)

        updated = Relation(rel)
        updated.setMembers(new_members)
        commands.add(ChangeCommand(rel, updated))

    if not commands.isEmpty():
        getInstance().add(SequenceCommand("outer→inner role fix", commands))

        # Notify JOSM of changes
        for rid in planned.keys():
            rel = rel_lookup.get(rid)
            if rel is not None:
                rel.setModified(True)
        layer.setModified(True)
        MainApplication.getMap().repaint()

        JOptionPane.showMessageDialog(
            None,
            "Updated {} relations ({} way role changes).".format(
                commands.size(),
                sum(len(v) for v in planned.values())
            ),
            "Done",
            JOptionPane.INFORMATION_MESSAGE
        )
    else:
        JOptionPane.showMessageDialog(
            None,
            "Nothing to apply.",
            "Nothing to do",
            JOptionPane.INFORMATION_MESSAGE
        )
