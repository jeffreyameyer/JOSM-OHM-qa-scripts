# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script converts "century dates" like C19 or C7 in start_date & end_date tags
# it converts start_dates to the earliest year of that century, so 1800 for start_date=C19
# and end_dates to the last year of the century, so 0699 for end_date=C7
# start_date:edtf and end_date:edtf are both set to YYXX, so 18XX for C19 and 06XX for C7
# the original key values are stored in start_date:raw and end_date:raw for comparison

import re
from java.util import ArrayList
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance

def century_to_year(digits, is_end_date=False):
    """Convert century digit string to YY00 / 0Y00 (start) or YY99 / 0Y99 (end)."""
    n = int(digits)
    century_minus_1 = n - 1
    suffix = "99" if is_end_date else "00"
    if len(digits) == 2:
        return "{:02d}{}".format(century_minus_1, suffix)
    else:
        return "0{:d}{}".format(century_minus_1, suffix)

def century_to_edtf(digits):
    """Convert century digit string to EDTF form: 18XX, 08XX, etc."""
    n = int(digits)
    century_minus_1 = n - 1
    if len(digits) == 2:
        return "{:02d}XX".format(century_minus_1)
    else:
        return "0{:d}XX".format(century_minus_1)

# Negative lookahead (?!\d) prevents matching C19 inside C1920
CENTURY_RE = re.compile(r'C(\d{2}|\d{1})(?!\d)')

# Matches values that are *only* a century token (nothing else)
CENTURY_ONLY_RE = re.compile(r'^C(\d{2}|\d{1})$')

layer = MainApplication.getLayerManager().getEditLayer()
if layer is None:
    raise Exception("No active edit layer.")

ds = layer.data
selected = list(ds.getSelected())

if not selected:
    print("No objects selected.")
else:
    all_commands = ArrayList()
    processed = 0

    for obj in selected:
        for date_key in ("start_date", "end_date"):
            original_value = obj.get(date_key)
            if original_value is None:
                continue

            if not CENTURY_RE.search(original_value):
                continue

            is_end = (date_key == "end_date")

            def replacer(match, is_end=is_end):
                digits = match.group(1)
                return century_to_year(digits, is_end_date=is_end)

            new_value = CENTURY_RE.sub(replacer, original_value)
            raw_key = date_key + ":raw"
            edtf_key = date_key + ":edtf"

            # 1. Copy original to :raw
            all_commands.add(ChangePropertyCommand([obj], raw_key, original_value))

            # 2. Write transformed value back to original key
            all_commands.add(ChangePropertyCommand([obj], date_key, new_value))

            # 3. If the value is *only* a century token, also write :edtf
            only_match = CENTURY_ONLY_RE.match(original_value)
            if only_match:
                digits = only_match.group(1)
                edtf_value = century_to_edtf(digits)
                all_commands.add(ChangePropertyCommand([obj], edtf_key, edtf_value))
                print(u"  {} '{}' -> '{}', :raw='{}', :edtf='{}'".format(
                    date_key, original_value, new_value, original_value, edtf_value))
            else:
                print(u"  {} '{}' -> '{}', :raw='{}'".format(
                    date_key, original_value, new_value, original_value))

            processed += 1

    if all_commands.size() > 0:
        getInstance().add(SequenceCommand("Fix century date tags", all_commands))
        print("Done. Processed {} date tag(s).".format(processed))
    else:
        print("No century-style date tags found in selection.")
