# Claude.ai-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script should catch all _selected_ objects that have 
# a date range of any type enclosed in () in any name:XX=* key
# BUT only if the dates in the name key match the values in the start_date and end_date keys
# if they do NOT match, a name:fixme key is set 
# with a message asking for the differences to be resolved
# NOTE: admin_level=1 objects are excluded

import re
from javax.swing import JOptionPane, JScrollPane, JTextArea
from java.awt import Dimension
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance

layer = MainApplication.getLayerManager().getEditLayer()
ds = layer.getDataSet()

commands = []
output = []

date_pattern = re.compile(r"""
    \s*\(\s*
    (?:
        # full date to full date: 1776-07-04-1880-10-01
        \d{1,4}-\d{2}-\d{2}\s*-\s*\d{1,4}-\d{2}-\d{2}
        |
        # full date to year-month: 1776-07-04-1880-10
        \d{1,4}-\d{2}-\d{2}\s*-\s*\d{1,4}-\d{2}
        |
        # year-month to full date: 1776-07-1880-10-01
        \d{1,4}-\d{2}\s*-\s*\d{1,4}-\d{2}-\d{2}
        |
        # full date to year: 1776-07-04-1880
        \d{1,4}-\d{2}-\d{2}\s*-\s*\d{1,4}
        |
        # year to full date: 1776-1880-07-04
        \d{1,4}\s*-\s*\d{1,4}-\d{2}-\d{2}
        |
        # full date only: 1776-07-04
        \d{1,4}-\d{2}-\d{2}
        |
        # year-month to year-month: 1776-07-1880-10
        \d{1,4}-\d{2}\s*-\s*\d{1,4}-\d{2}
        |
        # year-month to year: 1776-07-1880
        \d{1,4}-\d{2}\s*-\s*\d{1,4}
        |
        # year to year-month: 1776-1880-07
        \d{1,4}\s*-\s*\d{1,4}-\d{2}
        |
        # year-month only: 1776-07
        \d{1,4}-\d{2}
        |
        # year to year: 1776-1880
        \d{1,4}\s*-\s*\d{1,4}
        |
        # year with open end: 1776-
        \d{1,4}\s*-\s*
        |
        # year only: 1776
        \d{1,4}
    )
    \s*\)
""", re.VERBOSE)

name_key_pattern = re.compile(r'^name(:[a-z]{2}([_-][a-zA-Z]+)?)?$')
full_date_re = re.compile(r'\d{1,4}[-/]\d{2}[-/]\d{2}')
year_month_re = re.compile(r'\d{1,4}[-/]\d{2}(?![-/]\d{2})')
year_only_re = re.compile(r'\b\d{1,4}\b')
unpadded_full_date_re = re.compile(r'^\s*(\d{1,3})([-/]\d{2}[-/]\d{2})\s*$')
unpadded_year_re = re.compile(r'^\s*(\d{1,3})\s*$')

def normalize_year(year_str):
    return year_str.strip().zfill(4)

def normalize_date_tag(value):
    if not value:
        return value
    m = unpadded_full_date_re.match(value)
    if m:
        parts = value.strip().replace('/', '-').split('-')
        parts[0] = normalize_year(parts[0])
        return '-'.join(parts)
    m = unpadded_year_re.match(value)
    if m:
        return normalize_year(m.group(1))
    return value

def extract_dates(value):
    if not value:
        return {'full': set(), 'year_month': set(), 'years': set()}
    full_dates = set(full_date_re.findall(value))
    normalized_full = set()
    for d in full_dates:
        d = d.replace('/', '-')
        parts = d.split('-')
        parts[0] = normalize_year(parts[0])
        normalized_full.add('-'.join(parts))
    year_months = set(year_month_re.findall(value))
    normalized_ym = set()
    for ym in year_months:
        ym = ym.replace('/', '-')
        parts = ym.split('-')
        parts[0] = normalize_year(parts[0])
        normalized_ym.add('-'.join(parts))
    remaining = full_date_re.sub('', value)
    remaining = year_month_re.sub('', remaining)
    all_years = set(normalize_year(y) for y in year_only_re.findall(remaining))
    return {'full': normalized_full, 'year_month': normalized_ym, 'years': all_years}

def dates_match(name_dates, tag_dates):
    tag_ym_from_full = set('-'.join(d.split('-')[:2]) for d in tag_dates['full'] if len(d.split('-')) >= 2)
    tag_years_from_full = set(d.split('-')[0] for d in tag_dates['full'] if d)
    tag_years_from_ym = set(ym.split('-')[0] for ym in tag_dates['year_month'] if ym)
    all_tag_yms = tag_dates['year_month'] | tag_ym_from_full
    all_tag_years = tag_dates['years'] | tag_years_from_full | tag_years_from_ym | set(ym.split('-')[0] for ym in all_tag_yms)

    if name_dates['full']:
        return name_dates['full'].issubset(tag_dates['full'])
    if name_dates['year_month']:
        return name_dates['year_month'].issubset(all_tag_yms)
    if name_dates['years']:
        return name_dates['years'].issubset(all_tag_years)
    return False

def _split_date_range(inner):
    m = re.match(r'^(\d{1,4}-\d{2}-\d{2})-(\d{1,4}-\d{2}-\d{2})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4}-\d{2}-\d{2})-(\d{1,4}-\d{2})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4}-\d{2})-(\d{1,4}-\d{2}-\d{2})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4}-\d{2}-\d{2})-(\d{1,4})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4})-(\d{1,4}-\d{2}-\d{2})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4}-\d{2})-(\d{1,4}-\d{2})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4}-\d{2})-(\d{1,4})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4})-(\d{1,4}-\d{2})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4})-(\d{1,4})$', inner)
    if m: return [m.group(1), m.group(2)]
    m = re.match(r'^(\d{1,4})-$', inner)
    if m: return [m.group(1)]
    return [inner]

def extract_dates_from_patterns(value):
    dates = {'full': set(), 'year_month': set(), 'years': set()}
    for match in date_pattern.finditer(value):
        inner = match.group().strip().lstrip('(').rstrip(')').strip()
        if ' - ' in inner:
            parts = inner.split(' - ', 1)
        else:
            parts = _split_date_range(inner)
        for part in parts:
            part = part.strip().rstrip('-').strip()
            if part:
                d = extract_dates(part)
                dates['full'].update(d['full'])
                dates['year_month'].update(d['year_month'])
                dates['years'].update(d['years'])
    return dates

def get_primitive_type(obj):
    from org.openstreetmap.josm.data.osm import Node, Way, Relation
    if isinstance(obj, Node):
        return "node"
    elif isinstance(obj, Way):
        return "way"
    elif isinstance(obj, Relation):
        return "relation"
    return "unknown"

def format_date_tags(start_date, end_date):
    parts = []
    if start_date:
        parts.append(u"start_date={}".format(start_date))
    if end_date:
        parts.append(u"end_date={}".format(end_date))
    return u" | ".join(parts)

def process_objects(objects):
    for obj in objects:
        start_date = obj.get("start_date")
        end_date = obj.get("end_date")

        if not start_date and not end_date:
            continue

        obj_id = obj.getId()
        obj_type = get_primitive_type(obj)
        name = obj.get("name") or u""
        date_tags = format_date_tags(start_date, end_date)

        for tag in ["start_date", "end_date"]:
            tag_value = obj.get(tag)
            if tag_value:
                normalized = normalize_date_tag(tag_value)
                if normalized != tag_value:
                    output.append(u"PAD {} [{}] \"{}\" [{}]: {} {} -> {}".format(
                        obj_type, obj_id, name, date_tags, tag, tag_value, normalized))
                    commands.append(ChangePropertyCommand(obj, tag, normalized))

        start_date_norm = normalize_date_tag(start_date)
        end_date_norm = normalize_date_tag(end_date)

        tag_dates = {'full': set(), 'year_month': set(), 'years': set()}
        for tag_value in [start_date_norm, end_date_norm]:
            d = extract_dates(tag_value)
            tag_dates['full'].update(d['full'])
            tag_dates['year_month'].update(d['year_month'])
            tag_dates['years'].update(d['years'])

        for key in obj.keySet():
            if name_key_pattern.match(key):
                value = obj.get(key)
                if not value:
                    continue

                name_dates = extract_dates_from_patterns(value)
                if not name_dates['full'] and not name_dates['year_month'] and not name_dates['years']:
                    continue

                new_value = date_pattern.sub('', value).strip()

                if dates_match(name_dates, tag_dates):
                    output.append(u"STRIP {} [{}] \"{}\" [{}]: {} -> {}".format(
                        obj_type, obj_id, name, date_tags, value, new_value))
                    commands.append(ChangePropertyCommand(obj, key, new_value))
                else:
                    output.append(u"MISMATCH {} [{}] \"{}\" [{}]: name dates={} tag dates={}".format(
                        obj_type, obj_id, name, date_tags, name_dates, tag_dates))
                    commands.append(ChangePropertyCommand(obj, "name:fixme",
                        "double-check the dates in the name tag and make sure they match the start_date and end_date tags. It is impossible to know which are correct without further research."))

process_objects(ds.getNodes())
process_objects(ds.getWays())
process_objects(ds.getRelations())

if commands:
    getInstance().add(SequenceCommand("Strip dates from names", commands))
    output.append(u"Done: {} tags updated".format(len(commands)))
else:
    output.append(u"No matching objects found")

text_area = JTextArea("\n".join(output))
text_area.setEditable(False)
scroll_pane = JScrollPane(text_area)
scroll_pane.setPreferredSize(Dimension(600, 400))
JOptionPane.showMessageDialog(None, scroll_pane, "Strip Dates from Names", JOptionPane.INFORMATION_MESSAGE)
