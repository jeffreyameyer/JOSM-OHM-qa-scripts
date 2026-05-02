# Claude-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM

# OHM: Migrate mapwarper URLs.
#
# Rule 1 (URL key): source:[N:]url=https://[www.]mapwarper.net/maps/tile/<ID>/{z}/{x}/{y}.png
#   -> add source:[N:]tiles = original tile URL
#      rewrite source:[N:]url = https://mapwarper.net/maps/<ID>
#
# Rule 2 (bare source key): source[:N]=https://[www.]mapwarper.net/maps/<ID>
#   -> add source:[N:]tiles = https://mapwarper.net/maps/tile/<ID>/{z}/{x}/{y}.png
#      (source[:N] value is left unchanged)
#
# In both rules, <ID> is 1–6 digits and "www." is optional in the input.
# If the corresponding source:[N:]tiles tag already exists, the rule is skipped (NO CHANGE).
#
# Operates on the current selection. Shows a review dialog before applying.
# Run from JOSM's Scripting console (Jython).

import re

from java.awt import Dimension
from javax.swing import JOptionPane, JScrollPane, JTable, JPanel, JLabel, BoxLayout
from javax.swing.table import DefaultTableModel

from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.data import UndoRedoHandler
from org.openstreetmap.josm.gui import MainApplication


# Rule 1 — key: source:url OR source:<discriminator>:url
URL_KEY_RE = re.compile(r"^source(:.+)?:url$")
# Rule 1 — value: tile URL with optional www., capturing the ID.
URL_VALUE_RE = re.compile(
    r"^https://(?:www\.)?mapwarper\.net/maps/tile/([0-9]{1,6})/\{z\}/\{x\}/\{y\}\.png$"
)

# Rule 2 — key: source OR source:<discriminator>
BARE_KEY_RE = re.compile(r"^source(:.+)?$")
# Rule 2 — value: bare map URL with optional www., capturing the ID.
BARE_VALUE_RE = re.compile(
    r"^https://(?:www\.)?mapwarper\.net/maps/([0-9]{1,6})$"
)


def find_candidates(obj):
    """
    Return a list of dicts describing each candidate change for this object:
      {
        'rule': 1 or 2,
        'src_key': key being inspected,
        'src_old_value': current value of src_key,
        'src_new_value': new value for src_key (None if unchanged),
        'tiles_key': target source:[N:]tiles key,
        'tiles_value': value to write to tiles_key,
      }
    """
    candidates = []
    keys = list(obj.keySet())

    for key in keys:
        value = obj.get(key)
        if value is None:
            continue

        # Rule 1: source:[N:]url with a tile-URL value.
        m_key = URL_KEY_RE.match(key)
        if m_key:
            m_val = URL_VALUE_RE.match(value)
            if m_val:
                discriminator = m_key.group(1) or ""
                map_id = m_val.group(1)
                candidates.append({
                    'rule': 1,
                    'src_key': key,
                    'src_old_value': value,
                    'src_new_value': "https://mapwarper.net/maps/{0}".format(map_id),
                    'tiles_key': "source{0}:tiles".format(discriminator),
                    'tiles_value': value,
                })
                continue

        # Rule 2: source[:N] with a bare map-URL value.
        # Exclude keys that end in :url, :tiles, etc. — only the plain "source" or "source:<N>" form.
        if key == "source" or (
            BARE_KEY_RE.match(key) and not URL_KEY_RE.match(key) and not key.endswith(":tiles")
        ):
            # We need to be careful: BARE_KEY_RE matches anything starting with "source".
            # Restrict Rule 2 to keys that are exactly "source" or "source:<discriminator>"
            # where the discriminator does not itself contain further colons that would imply
            # a sub-namespace like :url or :tiles. We'll allow any discriminator that doesn't
            # equal a known suffix — practically, exclude :url and :tiles since those are
            # the namespaces we manage.
            if key.endswith(":url") or key.endswith(":tiles"):
                continue
            m_val = BARE_VALUE_RE.match(value)
            if not m_val:
                continue
            m_key2 = BARE_KEY_RE.match(key)
            discriminator = m_key2.group(1) or ""
            map_id = m_val.group(1)
            candidates.append({
                'rule': 2,
                'src_key': key,
                'src_old_value': value,
                'src_new_value': None,  # leave the source value unchanged
                'tiles_key': "source{0}:tiles".format(discriminator),
                'tiles_value': "https://mapwarper.net/maps/tile/{0}/{{z}}/{{x}}/{{y}}.png".format(map_id),
            })

    return candidates


def obj_label(obj):
    return "{0} {1}".format(obj.getType().getAPIName(), obj.getId())


def show_review_dialog(review_rows, change_count, skip_count):
    columns = ["Object", "Rule", "Action", "Key", "Old value", "New value"]
    model = DefaultTableModel(columns, 0)
    for row in review_rows:
        model.addRow(list(row))

    table = JTable(model)
    table.setAutoCreateRowSorter(True)
    table.setFillsViewportHeight(True)
    widths = [110, 50, 90, 180, 360, 360]
    for i, w in enumerate(widths):
        table.getColumnModel().getColumn(i).setPreferredWidth(w)

    scroll = JScrollPane(table)
    scroll.setPreferredSize(Dimension(1200, 460))

    header_lines = ["{0} tag change(s) will be applied.".format(change_count)]
    if skip_count:
        header_lines.append(
            "{0} tag(s) marked NO CHANGE because :tiles already exists (will not be overwritten).".format(skip_count)
        )
    header_lines.append("")
    header_lines.append("Click OK to apply the changes, Cancel to abort.")

    panel = JPanel()
    panel.setLayout(BoxLayout(panel, BoxLayout.Y_AXIS))
    for line in header_lines:
        panel.add(JLabel(line))
    panel.add(scroll)

    result = JOptionPane.showConfirmDialog(
        None,
        panel,
        "Review mapwarper URL migration",
        JOptionPane.OK_CANCEL_OPTION,
        JOptionPane.PLAIN_MESSAGE,
    )
    return result == JOptionPane.OK_OPTION


def main():
    layer = MainApplication.getLayerManager().getEditLayer()
    if layer is None:
        JOptionPane.showMessageDialog(None, "No active OSM data layer.")
        return

    dataset = layer.getDataSet()
    selection = dataset.getSelected()
    if not selection:
        JOptionPane.showMessageDialog(None, "Nothing selected. Select objects first.")
        return

    proposed = []     # list of (obj, candidate) to apply
    review_rows = []  # all rows shown in the dialog
    change_count = 0
    skip_count = 0

    for obj in selection:
        candidates = find_candidates(obj)
        if not candidates:
            continue
        label = obj_label(obj)
        for cand in candidates:
            rule_label = "R{0}".format(cand['rule'])
            existing_tiles = obj.get(cand['tiles_key'])
            if existing_tiles is not None:
                # Skip the entire rule application; do not overwrite :tiles, do not rewrite src.
                review_rows.append((
                    label, rule_label, "NO CHANGE", cand['tiles_key'],
                    existing_tiles, "(kept; :tiles already present)"
                ))
                skip_count += 1
                if cand['src_new_value'] is not None:
                    review_rows.append((
                        label, rule_label, "NO CHANGE", cand['src_key'],
                        cand['src_old_value'], "(kept; :tiles already present)"
                    ))
                    skip_count += 1
                continue

            proposed.append((obj, cand))
            review_rows.append((
                label, rule_label, "Add", cand['tiles_key'], "(none)", cand['tiles_value']
            ))
            change_count += 1
            if cand['src_new_value'] is not None:
                review_rows.append((
                    label, rule_label, "Rewrite", cand['src_key'],
                    cand['src_old_value'], cand['src_new_value']
                ))
                change_count += 1

    if not review_rows:
        JOptionPane.showMessageDialog(None, "No matching tags found in selection.")
        return

    if change_count == 0:
        JOptionPane.showMessageDialog(
            None,
            "All matching tags already have :tiles set. No changes to apply.\n\n"
            "{0} tag(s) marked NO CHANGE.".format(skip_count),
        )
        return

    if not show_review_dialog(review_rows, change_count, skip_count):
        JOptionPane.showMessageDialog(None, "Cancelled. No changes applied.")
        return

    commands = []
    affected_objs = set()
    for obj, cand in proposed:
        commands.append(ChangePropertyCommand([obj], cand['tiles_key'], cand['tiles_value']))
        if cand['src_new_value'] is not None:
            commands.append(ChangePropertyCommand([obj], cand['src_key'], cand['src_new_value']))
        affected_objs.add(obj)

    seq = SequenceCommand("Migrate mapwarper URLs to :tiles", commands)
    UndoRedoHandler.getInstance().add(seq)

    summary = "Updated {0} object(s); issued {1} tag change(s).".format(
        len(affected_objs), len(commands)
    )
    if skip_count:
        summary += "\n\n{0} tag(s) skipped (NO CHANGE — :tiles already existed).".format(skip_count)
    JOptionPane.showMessageDialog(None, summary)


main()
