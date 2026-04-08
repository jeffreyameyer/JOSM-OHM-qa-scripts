# Written by Claude 
# With prompting from Jeff Meyer
# Notes: This does *not* catch naked years or any month/day-specific ranges in parens
# Users should test this against their data locally before uploading to OHM

import re
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.UndoRedoHandler import getInstance

layer = MainApplication.getLayerManager().getEditLayer()
ds = layer.getDataSet()

commands = []
date_pattern = re.compile(r'\s*\(\s*(\d{4}\s*-\s*(\d{4}|\d{2})?\s*|\d{4}\s*)\)')
name_key_pattern = re.compile(r'^name(:[a-z]{2}([_-][a-zA-Z]+)?)?$')

for relation in ds.getRelations():
    for key in relation.keySet():
        if name_key_pattern.match(key):
            value = relation.get(key)
            if value and date_pattern.search(value):
                new_value = date_pattern.sub('', value).strip()
                print(u"{} {}: {} -> {}".format(key, relation.getId(), value, new_value))
                commands.append(ChangePropertyCommand(relation, key, new_value))

if commands:
    getInstance().add(SequenceCommand("Strip dates from relation names", commands))
    print("Done: {} tags updated".format(len(commands)))
else:
    print("No matching relations found")
