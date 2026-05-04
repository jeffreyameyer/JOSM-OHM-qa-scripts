# Claude-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM

# OHM: Migrate mapwarper URLs.
#
# Rule 1 (tile URL): value matches https://[www.]mapwarper.net/maps/tile/<ID>/{z}/{x}/{y}.png
#   Case A — found on source:[N:]url:
#     -> add source:[N:]tiles = original tile URL
#        rewrite source:[N:]url = https://mapwarper.net/maps/<ID>
#   Case B — found on bare source[:N]:
#     -> add source:[N:]tiles = original tile URL
#        if source:[N:]url is empty/absent: write canonical there
#        else: move existing source:[N:]url to source:[N:]url:2 (overwriting if present),
#              then write canonical to source:[N:]url
#        remove the bare source[:N] key (its content is now split into :tiles and :url)
#
# Rule 2 (bare canonical URL): source[:N]=https://[www.]mapwarper.net/maps/<ID>
#   -> add source:[N:]tiles = https://mapwarper.net/maps/tile/<ID>/{z}/{x}/{y}.png
#      (source[:N] value is left unchanged)
#
# Rule 3 (name lookup): whenever a mapwarper URL (tile or canonical) is found on
# source:[N:]url or bare source[:N], and source:[N:]name is empty/absent,
# fetch the map's title from https://mapwarper.net/maps/<ID>.json and write it
# to source:[N:]name. Other lookup failures are shown as NO CHANGE rows.
#
# Rule 4 (missing map flag): if the lookup returns HTTP 404, append
# "no such mapwarper map <ID>" to the object's fixme:tiles=* tag (creating it
# if absent). This is a dedicated key so it stands out and doesn't conflict
# with any existing fixme=* content. Duplicate entries for the same <ID> are
# skipped. Rules 1 and 2 still apply as normal — the URLs get migrated, the
# object just gets flagged.
#
# In all rules, <ID> is 1–6 digits and "www." is optional in the input.
# If the corresponding source:[N:]tiles tag already exists for Rules 1/2, that rule is skipped.
#
# Operates on the current selection. Shows a review dialog before applying.
# Run from JOSM's Scripting console (Jython).

import re

from java.awt import Dimension
from java.lang import Throwable
from java.net import URL, HttpURLConnection
from javax.swing import JOptionPane, JScrollPane, JTable, JPanel, JLabel, BoxLayout
from javax.swing.table import DefaultTableModel

from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.data import UndoRedoHandler
from org.openstreetmap.josm.gui import MainApplication


# Tile URL value: with optional www., capturing the ID.
TILE_VALUE_RE = re.compile(
    r"^https://(?:www\.)?mapwarper\.net/maps/tile/([0-9]{1,6})/\{z\}/\{x\}/\{y\}\.png$"
)
# Bare canonical URL value: with optional www., capturing the ID.
CANONICAL_VALUE_RE = re.compile(
    r"^https://(?:www\.)?mapwarper\.net/maps/([0-9]{1,6})$"
)

# Key shape: source:url OR source:<discriminator>:url
URL_KEY_RE = re.compile(r"^source(:.+)?:url$")
# Key shape: source OR source:<discriminator> (a "bare" source key)
# Note: must be filtered to exclude :url, :tiles, :name, :url:2 etc. by the caller.
BARE_KEY_RE = re.compile(r"^source(:.+)?$")

# Suffixes that disqualify a key from being treated as "bare source"
BARE_DISQUALIFYING_SUFFIXES = (":url", ":tiles", ":name", ":url:2")

# Cache of map_id -> (title_or_None, error_or_None, status_or_None) for this run.
# status is the HTTP status code if we got one (e.g. 404), or None for other failure modes.
_NAME_CACHE = {}


def canonical_url(map_id):
    return "https://mapwarper.net/maps/{0}".format(map_id)


def tile_url(map_id):
    return "https://mapwarper.net/maps/tile/{0}/{{z}}/{{x}}/{{y}}.png".format(map_id)


def discriminator_for_url_key(key):
    """Given source:url or source:<N>:url, return ':<N>' or ''."""
    m = URL_KEY_RE.match(key)
    return m.group(1) or "" if m else ""


def discriminator_for_bare_key(key):
    """Given source or source:<N>, return ':<N>' or ''."""
    m = BARE_KEY_RE.match(key)
    return m.group(1) or "" if m else ""


def is_bare_source_key(key):
    """True for plain 'source' or 'source:<discriminator>' that is not a managed sub-namespace."""
    if not BARE_KEY_RE.match(key):
        return False
    for suf in BARE_DISQUALIFYING_SUFFIXES:
        if key.endswith(suf):
            return False
    return True


def _read_stream_to_string(stream):
    """Read a Java InputStream fully and return it as a UTF-8 Python str."""
    from java.io import ByteArrayOutputStream
    buf = ByteArrayOutputStream()
    chunk = bytearray(8192)
    try:
        while True:
            n = stream.read(chunk)
            if n == -1:
                break
            buf.write(chunk, 0, n)
    finally:
        try:
            stream.close()
        except Throwable:
            pass
    return bytes(buf.toByteArray()).decode("utf-8", "replace")


def fetch_mapwarper_title(map_id):
    """
    Fetch the map's title from https://mapwarper.net/maps/<ID>.json.
    Returns (title, error, status) where:
      - title:  the map's title on success, else None
      - error:  human-readable error string on failure, else None
      - status: HTTP status code if the request completed (e.g. 404), else None
    Cached per run.

    Note: must catch java.lang.Throwable, not just Python's Exception, because
    Jython does not bridge Java exceptions into Python's Exception hierarchy
    in the way you'd expect (e.g. java.io.FileNotFoundException on a 404).
    """
    if map_id in _NAME_CACHE:
        return _NAME_CACHE[map_id]

    url_str = "https://mapwarper.net/maps/{0}.json".format(map_id)
    result = (None, "unknown error", None)
    conn = None
    try:
        url_obj = URL(url_str)
        conn = url_obj.openConnection()
        # Cast hint: openConnection on an https URL returns an HttpsURLConnection,
        # which is a subclass of HttpURLConnection. We can call HttpURLConnection
        # methods on it directly without an explicit cast in Jython.
        conn.setConnectTimeout(10000)
        conn.setReadTimeout(15000)
        conn.setInstanceFollowRedirects(True)
        conn.setRequestProperty("User-Agent", "JOSM-OHM-mapwarper-migrate/1.0")
        conn.setRequestProperty("Accept", "application/json")

        # Trigger the request and check status BEFORE getInputStream(), because
        # getInputStream() throws FileNotFoundException on any 4xx response.
        status = conn.getResponseCode()

        if status >= 200 and status < 300:
            stream = conn.getInputStream()
            body_str = _read_stream_to_string(stream)

            import json
            data = json.loads(body_str)

            # Mapwarper's /maps/<id>.json sometimes wraps the map object under
            # "map" and sometimes returns it at the top level. Handle both.
            title = None
            if isinstance(data, dict):
                if data.get("title"):
                    title = data["title"]
                elif isinstance(data.get("map"), dict) and data["map"].get("title"):
                    title = data["map"]["title"]

            if not title:
                result = (None, "no title in response", status)
            else:
                title = " ".join(str(title).split())
                result = (title, None, status)
        else:
            # Non-2xx: drain error stream for diagnostics but don't fail on it.
            err_snippet = ""
            try:
                err_stream = conn.getErrorStream()
                if err_stream is not None:
                    err_body = _read_stream_to_string(err_stream)
                    err_snippet = err_body[:120].replace("\n", " ").strip()
            except Throwable:
                pass
            if status == 404:
                result = (None, "HTTP 404 (map not found)", status)
            elif status == 401 or status == 403:
                result = (None, "HTTP {0} (auth required)".format(status), status)
            else:
                msg = "HTTP {0}".format(status)
                if err_snippet:
                    msg = "{0}: {1}".format(msg, err_snippet)
                result = (None, msg, status)
    except Throwable as e:
        # Catches Java exceptions (FileNotFoundException, SocketTimeoutException,
        # SSLHandshakeException, UnknownHostException, etc.).
        result = (None, "java error: {0}".format(e), None)
    except Exception as e:
        # Catches Python-level errors (json.loads, decoding, etc.).
        result = (None, "error: {0}".format(e), None)
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Throwable:
                pass

    _NAME_CACHE[map_id] = result
    return result


def find_candidates(obj):
    """
    Return a list of dicts describing each candidate change for this object:
      {
        'rule': 1, 2, or 3,
        ... rule-specific fields ...
      }
    Rule 1/2 entries describe URL-tag migrations.
    Rule 3 entries are added per (discriminator, map_id) pair where source:[N:]name is empty.
    """
    candidates = []
    keys = list(obj.keySet())

    # Track (discriminator, map_id) pairs found on this object so Rule 3 can fire.
    name_targets = []  # list of (discriminator, map_id)

    for key in keys:
        value = obj.get(key)
        if value is None:
            continue

        # Rule 1 Case A: source:[N:]url with tile-URL value.
        if URL_KEY_RE.match(key):
            m_val = TILE_VALUE_RE.match(value)
            if m_val:
                disc = discriminator_for_url_key(key)
                map_id = m_val.group(1)
                candidates.append({
                    'rule': 1,
                    'case': 'A',
                    'src_key': key,
                    'tiles_key': "source{0}:tiles".format(disc),
                    'tiles_value': value,
                    'url_key': key,  # same key, rewriting it
                    'url_new_value': canonical_url(map_id),
                    'url_old_value': value,
                    'discriminator': disc,
                    'map_id': map_id,
                })
                name_targets.append((disc, map_id))
                continue
            # Also detect a canonical URL sitting on :url (no migration, but fuels Rule 3).
            m_canon = CANONICAL_VALUE_RE.match(value)
            if m_canon:
                disc = discriminator_for_url_key(key)
                name_targets.append((disc, m_canon.group(1)))
            continue

        # Rule 1 Case B / Rule 2: bare source key.
        if is_bare_source_key(key):
            m_tile = TILE_VALUE_RE.match(value)
            if m_tile:
                disc = discriminator_for_bare_key(key)
                map_id = m_tile.group(1)
                url_key = "source{0}:url".format(disc)
                url_2_key = "source{0}:url:2".format(disc)
                existing_url = obj.get(url_key)
                candidates.append({
                    'rule': 1,
                    'case': 'B',
                    'src_key': key,           # the bare key, to be removed
                    'tiles_key': "source{0}:tiles".format(disc),
                    'tiles_value': value,
                    'url_key': url_key,
                    'url_new_value': canonical_url(map_id),
                    'url_old_value': existing_url,  # may be None
                    'url_2_key': url_2_key,
                    'url_2_new_value': existing_url,  # only used if existing_url is not None
                    'discriminator': disc,
                    'map_id': map_id,
                })
                name_targets.append((disc, map_id))
                continue

            m_canon = CANONICAL_VALUE_RE.match(value)
            if m_canon:
                disc = discriminator_for_bare_key(key)
                map_id = m_canon.group(1)
                candidates.append({
                    'rule': 2,
                    'src_key': key,
                    'src_old_value': value,
                    'tiles_key': "source{0}:tiles".format(disc),
                    'tiles_value': tile_url(map_id),
                    'discriminator': disc,
                    'map_id': map_id,
                })
                name_targets.append((disc, map_id))
                continue

    # Rule 3: dedupe (discriminator, map_id) pairs and check name.
    seen_discriminators = set()
    for disc, map_id in name_targets:
        if disc in seen_discriminators:
            continue
        seen_discriminators.add(disc)
        name_key = "source{0}:name".format(disc)
        existing_name = obj.get(name_key)
        if existing_name is not None and existing_name.strip() != "":
            continue  # already has a name; skip
        candidates.append({
            'rule': 3,
            'name_key': name_key,
            'discriminator': disc,
            'map_id': map_id,
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
    widths = [110, 50, 110, 200, 360, 360]
    for i, w in enumerate(widths):
        table.getColumnModel().getColumn(i).setPreferredWidth(w)

    scroll = JScrollPane(table)
    scroll.setPreferredSize(Dimension(1280, 480))

    header_lines = ["{0} tag change(s) will be applied.".format(change_count)]
    if skip_count:
        header_lines.append(
            "{0} tag(s) marked NO CHANGE (will not be modified).".format(skip_count)
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

    # First pass: collect all URL-rule candidates, and gather all (map_id) values
    # we'll need to look up for Rule 3, so we can fetch them up front.
    per_obj_candidates = []
    map_ids_needing_lookup = set()

    for obj in selection:
        cands = find_candidates(obj)
        if not cands:
            continue
        per_obj_candidates.append((obj, cands))
        for c in cands:
            if c['rule'] == 3:
                map_ids_needing_lookup.add(c['map_id'])

    if not per_obj_candidates:
        JOptionPane.showMessageDialog(None, "No matching tags found in selection.")
        return

    # Pre-fetch names for all map_ids that need a lookup. Cache populates _NAME_CACHE.
    for map_id in sorted(map_ids_needing_lookup):
        fetch_mapwarper_title(map_id)

    # Second pass: build review rows and proposed commands.
    proposed = []     # list of (obj, command_descriptor) — see below
    review_rows = []
    change_count = 0
    skip_count = 0

    for obj, cands in per_obj_candidates:
        label = obj_label(obj)
        for cand in cands:
            rule = cand['rule']
            rule_label = "R{0}".format(rule)

            if rule == 1:
                # Skip entirely if :tiles already present.
                existing_tiles = obj.get(cand['tiles_key'])
                if existing_tiles is not None:
                    review_rows.append((
                        label, rule_label, "NO CHANGE", cand['tiles_key'],
                        existing_tiles, "(kept; :tiles already present)"
                    ))
                    skip_count += 1
                    continue

                # Add :tiles
                review_rows.append((
                    label, rule_label, "Add", cand['tiles_key'],
                    "(none)", cand['tiles_value']
                ))
                change_count += 1

                if cand['case'] == 'A':
                    # Rewrite source:[N:]url to canonical.
                    review_rows.append((
                        label, rule_label, "Rewrite", cand['url_key'],
                        cand['url_old_value'], cand['url_new_value']
                    ))
                    change_count += 1
                    proposed.append(('R1A', obj, cand))
                else:
                    # Case B: bare key. Handle :url displacement, write canonical, remove bare.
                    if cand['url_old_value'] is not None:
                        review_rows.append((
                            label, rule_label, "Move", cand['url_2_key'],
                            obj.get(cand['url_2_key']) if obj.get(cand['url_2_key']) is not None else "(none)",
                            cand['url_2_new_value']
                        ))
                        change_count += 1
                        review_rows.append((
                            label, rule_label, "Rewrite", cand['url_key'],
                            cand['url_old_value'], cand['url_new_value']
                        ))
                        change_count += 1
                    else:
                        review_rows.append((
                            label, rule_label, "Add", cand['url_key'],
                            "(none)", cand['url_new_value']
                        ))
                        change_count += 1
                    review_rows.append((
                        label, rule_label, "Remove", cand['src_key'],
                        cand['tiles_value'], "(deleted)"
                    ))
                    change_count += 1
                    proposed.append(('R1B', obj, cand))

            elif rule == 2:
                existing_tiles = obj.get(cand['tiles_key'])
                if existing_tiles is not None:
                    review_rows.append((
                        label, rule_label, "NO CHANGE", cand['tiles_key'],
                        existing_tiles, "(kept; :tiles already present)"
                    ))
                    skip_count += 1
                    continue
                review_rows.append((
                    label, rule_label, "Add", cand['tiles_key'],
                    "(none)", cand['tiles_value']
                ))
                change_count += 1
                proposed.append(('R2', obj, cand))

            elif rule == 3:
                cached = _NAME_CACHE.get(cand['map_id'], (None, "not fetched", None))
                title, err, _status = cached
                if title:
                    review_rows.append((
                        label, rule_label, "Add", cand['name_key'],
                        "(none)", title
                    ))
                    change_count += 1
                    proposed.append(('R3', obj, cand, title))
                else:
                    review_rows.append((
                        label, rule_label, "NO CHANGE", cand['name_key'],
                        "(empty)", "(lookup failed: {0})".format(err)
                    ))
                    skip_count += 1

        # Rule 4 (per-object): if any map_id referenced by this object 404'd,
        # add a fixme tag noting the missing map(s).
        missing_ids = []
        seen_ids = set()
        for cand in cands:
            mid = cand.get('map_id')
            if mid is None or mid in seen_ids:
                continue
            seen_ids.add(mid)
            cached = _NAME_CACHE.get(mid)
            if cached is None:
                continue
            _t, _e, status = cached
            if status == 404:
                missing_ids.append(mid)

        if missing_ids:
            existing_fixme = obj.get("fixme:tiles")
            new_entries = []
            for mid in missing_ids:
                marker = "no such mapwarper map {0}".format(mid)
                if existing_fixme and marker in existing_fixme:
                    continue  # already noted
                if marker in new_entries:
                    continue
                new_entries.append(marker)

            if new_entries:
                added_text = "; ".join(new_entries)
                if existing_fixme:
                    new_fixme = "{0}; {1}".format(existing_fixme, added_text)
                else:
                    new_fixme = added_text
                review_rows.append((
                    label, "R4",
                    ("Append" if existing_fixme else "Add"),
                    "fixme:tiles",
                    existing_fixme if existing_fixme else "(none)",
                    new_fixme
                ))
                change_count += 1
                proposed.append(('R4', obj, new_fixme))

    if change_count == 0:
        JOptionPane.showMessageDialog(
            None,
            "No changes to apply.\n\n{0} tag(s) marked NO CHANGE.".format(skip_count),
        )
        return

    if not show_review_dialog(review_rows, change_count, skip_count):
        JOptionPane.showMessageDialog(None, "Cancelled. No changes applied.")
        return

    # Build commands.
    commands = []
    affected_objs = set()

    for entry in proposed:
        kind = entry[0]
        if kind == 'R1A':
            _, obj, cand = entry
            commands.append(ChangePropertyCommand([obj], cand['tiles_key'], cand['tiles_value']))
            commands.append(ChangePropertyCommand([obj], cand['url_key'], cand['url_new_value']))
            affected_objs.add(obj)
        elif kind == 'R1B':
            _, obj, cand = entry
            commands.append(ChangePropertyCommand([obj], cand['tiles_key'], cand['tiles_value']))
            if cand['url_old_value'] is not None:
                commands.append(ChangePropertyCommand([obj], cand['url_2_key'], cand['url_2_new_value']))
            commands.append(ChangePropertyCommand([obj], cand['url_key'], cand['url_new_value']))
            # Remove the bare source[:N] key by setting it to None.
            commands.append(ChangePropertyCommand([obj], cand['src_key'], None))
            affected_objs.add(obj)
        elif kind == 'R2':
            _, obj, cand = entry
            commands.append(ChangePropertyCommand([obj], cand['tiles_key'], cand['tiles_value']))
            affected_objs.add(obj)
        elif kind == 'R3':
            _, obj, cand, title = entry
            commands.append(ChangePropertyCommand([obj], cand['name_key'], title))
            affected_objs.add(obj)
        elif kind == 'R4':
            _, obj, new_fixme = entry
            commands.append(ChangePropertyCommand([obj], "fixme:tiles", new_fixme))
            affected_objs.add(obj)

    seq = SequenceCommand("Migrate mapwarper URLs (and lookup names)", commands)
    UndoRedoHandler.getInstance().add(seq)

    summary = "Updated {0} object(s); issued {1} tag change(s).".format(
        len(affected_objs), len(commands)
    )
    if skip_count:
        summary += "\n\n{0} tag(s) skipped (NO CHANGE).".format(skip_count)
    JOptionPane.showMessageDialog(None, summary)


main()
