# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script finds end_date or start_date tags in the format YYYYs
# and changes them to end_date=max(YYYYs) or start_date=min(YYYYs),
# sets either end_date:edtf or start_date:edtf to YXXX, YYXX, or YYYX, as appropriate
# and stores the original key value in end_date:raw or start_date:raw

from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance
from java.util import ArrayList
import re

CURRENT_YEAR = 2026

def count_trailing_zeros(n):
    s = str(n)
    return len(s) - len(s.rstrip('0'))

def parse_yyyys(value):
    m = re.match(r'^(\d{1,4})s$', value.strip())
    if not m:
        return None
    base_int = int(m.group(1))
    trailing = count_trailing_zeros(base_int)
    if trailing == 0:
        return None
    masked_digits = 2 if base_int == 2000 else trailing
    return (base_int, masked_digits)

def make_edtf(base_int, masked_digits):
    padded = '{:04d}'.format(base_int)
    return padded[:-masked_digits] + 'X' * masked_digits

def make_min_date(base_int):
    return '{:04d}'.format(base_int)

def make_max_date(base_int, masked_digits):
    padded = '{:04d}'.format(base_int)
    return padded[:-masked_digits] + '9' * masked_digits

def build_commands_for_object(obj):
    commands = []

    for prefix in ('start_date', 'end_date'):
        value = obj.get(prefix)
        if value is None:
            continue
        parsed = parse_yyyys(value)
        if parsed is None:
            continue

        base_int, masked_digits = parsed
        edtf = make_edtf(base_int, masked_digits)
        min_date = make_min_date(base_int)
        max_str = make_max_date(base_int, masked_digits)
        max_year = int(max_str)

        edtf_key = prefix + ':edtf'
        edtf_exists = obj.get(edtf_key) is not None

        # Always write the :raw key
        commands.append(ChangePropertyCommand([obj], prefix + ':raw', value))

        if prefix == 'start_date':
            commands.append(ChangePropertyCommand([obj], 'start_date', min_date))
            if edtf_exists:
                commands.append(ChangePropertyCommand([obj], 'start_date:edtf:compare', edtf))
            else:
                commands.append(ChangePropertyCommand([obj], 'start_date:edtf', edtf))

        elif prefix == 'end_date':
            if max_year > CURRENT_YEAR:
                commands.append(ChangePropertyCommand([obj], 'end_date', ''))
                if edtf_exists:
                    commands.append(ChangePropertyCommand(
                        [obj], 'end_date:edtf:compare',
                        'double check end_date:edtf; should it be blank?'
                    ))
            else:
                commands.append(ChangePropertyCommand([obj], 'end_date', max_str))
                if edtf_exists:
                    commands.append(ChangePropertyCommand([obj], 'end_date:edtf:compare', edtf))
                else:
                    commands.append(ChangePropertyCommand([obj], 'end_date:edtf', edtf))

    return commands

# --- Main ---
selected = list(MainApplication.getLayerManager().getEditDataSet().getSelected())

if not selected:
    print("No objects selected.")
else:
    all_commands = ArrayList()
    matched = 0

    for obj in selected:
        cmds = build_commands_for_object(obj)
        if cmds:
            for c in cmds:
                all_commands.add(c)
            matched += 1

    if all_commands.size() > 0:
        getInstance().add(
            SequenceCommand("Normalize YYYYs date tags", all_commands)
        )
        print("Updated {} object(s).".format(matched))
    else:
        print("No YYYYs date values found in selection.")
