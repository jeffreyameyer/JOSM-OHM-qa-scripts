# Claude-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM

"""
Batch validation script for OHM object IDs from a file.

Reads the first N object identifiers from an input file, downloads them
via JOSM's built-in Overpass download (using the configured OHM endpoint),
runs JOSM validation against the downloaded data, and writes a new file
containing the input minus the consumed IDs.

Input file format: one object per line, leading "[primitive initial]/[id]"
followed by ": irrelevant text". Example:

    n/2082831204: start_date:edtf=/1100/1130~ St Peter's Church
    w/123456: name=Some Way
    r/24680: type=multipolygon

Outputs use a monotonic 3-digit counter so successive runs are easy to
tell apart:
  <input>.001          remainder after the first run
  <input>.002          remainder after the second run
  ...
  <input>.unfixable_edtf.001   matching unfixable-EDTF values, run 1
  <input>.unfixable_edtf.002   ... run 2

If the chosen input already ends in ".NNN" (e.g. you re-feed the latest
remainder), that suffix is treated as a previous counter and the new
output continues the sequence -- no nested suffixes.

Conventions (per Jeff's JOSM/Jython environment):
- Diagnostics go to JOptionPane dialogs, not print().
- POSTs the query directly to the Overpass server URL configured in JOSM
  preferences (OHM's, in Jeff's setup) using JOSM's HttpClient. No
  bounding box -- objects are fetched by type+id only.
"""

import os
import re

from javax.swing import JOptionPane, JFileChooser

from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.gui.layer import OsmDataLayer
from org.openstreetmap.josm.data.validation import OsmValidator
from org.openstreetmap.josm.actions import ValidateAction

from java.lang import Runnable
from org.openstreetmap.josm.io import OverpassDownloadReader, OsmReader
from org.openstreetmap.josm.tools import HttpClient
from org.openstreetmap.josm.gui.progress import NullProgressMonitor

from java.util import ArrayList

from java.net import URL
from java.io import ByteArrayInputStream


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE = 1000

# Match the "n/123: ..." / "w/456: ..." / "r/789: ..." prefix.
ID_LINE_RE = re.compile(r"^\s*([nwr])\s*/\s*(\d+)\s*:")

TYPE_FROM_SHORT = {"n": "node", "w": "way", "r": "relation"}


def info(msg, title="Batch validate"):
    JOptionPane.showMessageDialog(None, msg, title, JOptionPane.INFORMATION_MESSAGE)


def warn(msg, title="Batch validate"):
    JOptionPane.showMessageDialog(None, msg, title, JOptionPane.WARNING_MESSAGE)


def error(msg, title="Batch validate"):
    JOptionPane.showMessageDialog(None, msg, title, JOptionPane.ERROR_MESSAGE)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def prompt_for_path():
    """Open a file chooser. Returns the absolute path or None if cancelled."""
    chooser = JFileChooser()
    chooser.setDialogTitle("Select object-ID file to batch-validate")
    chooser.setFileSelectionMode(JFileChooser.FILES_ONLY)
    chooser.setMultiSelectionEnabled(False)
    parent = MainApplication.getMainFrame()
    result = chooser.showOpenDialog(parent)
    if result != JFileChooser.APPROVE_OPTION:
        return None
    f = chooser.getSelectedFile()
    if f is None:
        return None
    return f.getAbsolutePath()


def prompt_for_batch_size(default=DEFAULT_BATCH_SIZE):
    """Ask the user for a batch size. Returns int, or None if cancelled.

    Empty input -> default. Non-positive or non-numeric input re-prompts.
    """
    while True:
        raw = JOptionPane.showInputDialog(
            None,
            "Number of objects to download per batch:",
            str(default),
        )
        if raw is None:
            return None  # cancelled
        raw = raw.strip()
        if not raw:
            return default
        try:
            n = int(raw)
        except ValueError:
            warn("Please enter a positive integer.")
            continue
        if n <= 0:
            warn("Batch size must be greater than zero.")
            continue
        return n


def parse_lines(lines):
    """Parse all lines.

    Returns (entries, parse_errors).
      entries: list of (otype, oid, original_line_index) for valid lines.
      parse_errors: list of (line_number, raw_line) for non-blank/non-#
        lines that didn't match the expected prefix.
    """
    entries = []
    parse_errors = []
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = ID_LINE_RE.match(raw)
        if not m:
            parse_errors.append((idx + 1, raw.rstrip("\n")))
            continue
        otype = TYPE_FROM_SHORT[m.group(1).lower()]
        oid = m.group(2)
        entries.append((otype, oid, idx))
    return entries, parse_errors


def read_input(path):
    f = open(path, "r")
    try:
        return f.readlines()
    finally:
        f.close()


# ---------------------------------------------------------------------------
# Output numbering
# ---------------------------------------------------------------------------

# Trailing ".NNN" (3+ digits) on an input path -- treated as a previous
# run's counter so the next run continues the sequence rather than
# nesting suffixes.
_COUNTER_SUFFIX_RE = re.compile(r"\.(\d{3,})$")

# Width of the zero-padded counter in output filenames.
COUNTER_WIDTH = 3


def strip_counter_suffix(path):
    """If path ends in ".NNN" (3+ digits), return (base, n).
    Otherwise return (path, None).
    """
    m = _COUNTER_SUFFIX_RE.search(path)
    if not m:
        return path, None
    return path[: m.start()], int(m.group(1))


def next_counter(base_path):
    """Find the next available counter for files named "<base_path>.NNN".

    Scans the directory of base_path for siblings matching that pattern
    and returns max(existing) + 1, or 1 if none exist.
    """
    directory = os.path.dirname(base_path) or "."
    base_name = os.path.basename(base_path)
    prefix = base_name + "."
    highest = 0
    try:
        for name in os.listdir(directory):
            if not name.startswith(prefix):
                continue
            tail = name[len(prefix):]
            m = re.match(r"^(\d{3,})$", tail)
            if not m:
                continue
            n = int(m.group(1))
            if n > highest:
                highest = n
    except OSError:
        pass
    return highest + 1


def numbered_path(base_path, counter):
    return "%s.%0*d" % (base_path, COUNTER_WIDTH, counter)


def write_remainder(base_path, counter, all_lines, consumed_line_indices):
    """Write the remainder to "<base_path>.NNN" (zero-padded)."""
    consumed = set(consumed_line_indices)
    out_path = numbered_path(base_path, counter)
    f = open(out_path, "w")
    try:
        for idx, line in enumerate(all_lines):
            if idx in consumed:
                continue
            f.write(line)
    finally:
        f.close()
    return out_path


# ---------------------------------------------------------------------------
# Overpass via JOSM
# ---------------------------------------------------------------------------

def build_overpass_query(batch):
    """Build an Overpass QL query for a batch of (otype, oid, _) tuples.

    Groups by type, uses id-list filters, recurses down for full geometry
    so ways/relations are usable for validation.
    """
    by_type = {"node": [], "way": [], "relation": []}
    for otype, oid, _ in batch:
        by_type[otype].append(oid)

    parts = ["[out:xml][timeout:180];", "("]
    if by_type["node"]:
        parts.append("  node(id:%s);" % ",".join(by_type["node"]))
    if by_type["way"]:
        parts.append("  way(id:%s);" % ",".join(by_type["way"]))
    if by_type["relation"]:
        parts.append("  relation(id:%s);" % ",".join(by_type["relation"]))
    parts.append(");")
    parts.append("(._;>;);")  # recurse down for full geometry
    parts.append("out meta;")
    return "\n".join(parts)


def download_via_josm(query):
    """POST the query to JOSM's configured Overpass server and parse.

    OverpassDownloadReader's constructor expects a Bounds, which doesn't
    fit an id-list query, so we use HttpClient to POST the query directly.
    The server URL still comes from JOSM preferences (the OHM endpoint,
    in Jeff's setup), so nothing is hardcoded.
    Returns a parsed DataSet.
    """
    server_url = OverpassDownloadReader.OVERPASS_SERVER.get()
    if not server_url.endswith("/"):
        server_url += "/"
    endpoint = server_url + "interpreter"

    url = URL(endpoint)
    client = HttpClient.create(url, "POST")
    client.setHeader("Content-Type", "application/x-www-form-urlencoded")
    from java.net import URLEncoder
    body = "data=" + URLEncoder.encode(query, "UTF-8")
    client.setRequestBody(body.encode("UTF-8"))

    response = client.connect()
    try:
        if response.getResponseCode() != 200:
            raise RuntimeError(
                "Overpass HTTP %d: %s"
                % (response.getResponseCode(), response.getResponseMessage())
            )
        xml_text = response.fetchContent()
        xml_bytes = xml_text.encode("UTF-8") if isinstance(xml_text, unicode) else xml_text
    finally:
        response.disconnect()

    stream = ByteArrayInputStream(xml_bytes)
    try:
        return OsmReader.parseDataSet(stream, NullProgressMonitor.INSTANCE)
    finally:
        stream.close()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def run_validation_async(layer, on_done):
    """Trigger JOSM's ValidateAction and call on_done(errors) when finished.

    ValidateAction.doValidate(true) enqueues a ValidationTask on
    MainApplication.worker. We then enqueue our own Runnable on the
    same worker; because the worker is single-threaded and FIFO, our
    task runs after validation completes. We harvest errors by walking
    the Validation Results tree (the same source backing the Validation
    Errors layer) and invoke on_done(errors) on the EDT.

    Note we do NOT block on the worker from the caller -- that would
    deadlock if the caller is on the EDT.
    """
    MainApplication.getLayerManager().setActiveLayer(layer)
    OsmValidator.initializeTests()

    # Kick off validation. doValidate(true) is the normal action path.
    try:
        ValidateAction().doValidate(True)
    except Exception as e:
        error("Could not start validation: %s" % e)
        return

    # Enqueue the continuation behind the validation task.
    class _After(Runnable):
        def run(self):
            errors = []
            try:
                errors = _harvest_validator_errors()
            except Exception as e:
                _ui_invoke(lambda: error("Could not read validation errors: %s" % e))
                return
            # Run the user's callback on the EDT so any Swing dialogs it
            # opens behave correctly.
            _ui_invoke(lambda: on_done(errors))

    try:
        MainApplication.worker.submit(_After())
    except Exception as e:
        error("Could not enqueue post-validation handler: %s" % e)


def _harvest_validator_errors():
    """Walk the Validation Results tree and return all TestError objects.

    The validator dialog's tree is the source of truth -- it's what the
    Validation Results panel shows and what backs the Validation Errors
    layer. Each tree node's userObject is either a grouping (string) or
    a TestError; we collect the TestErrors.
    """
    found = []
    try:
        vdialog = MainApplication.getMap().validatorDialog
    except Exception:
        return found
    if vdialog is None:
        return found
    try:
        root = vdialog.tree.getRoot()
    except Exception:
        return found
    if root is None:
        return found

    def walk(node):
        try:
            obj = node.getUserObject()
        except Exception:
            obj = None
        # TestError instances expose getMessage / getDescription / getPrimitives.
        if obj is not None and hasattr(obj, "getPrimitives"):
            found.append(obj)
        try:
            n = node.getChildCount()
        except Exception:
            n = 0
        for i in range(n):
            try:
                walk(node.getChildAt(i))
            except Exception:
                pass

    walk(root)
    return found


def _ui_invoke(fn):
    """Run fn() on the EDT (via SwingUtilities.invokeLater)."""
    from javax.swing import SwingUtilities
    class _R(Runnable):
        def run(self):
            try:
                fn()
            except Exception as e:
                # Last-resort: print to JOSM log; can't dialog from here.
                try:
                    from org.openstreetmap.josm.tools import Logging
                    Logging.warn("Post-validation handler failed: %s" % e)
                except Exception:
                    pass
    SwingUtilities.invokeLater(_R())


# The exact message title (case-insensitive substring match) for the
# group we want to extract values from. Visible in the validator panel as:
#   [ohm] Invalid date - *_date:edtf; unfixable, please review
UNFIXABLE_EDTF_TITLE_MARKER = "*_date:edtf; unfixable"

EDTF_KEYS = ("start_date:edtf", "end_date:edtf")


def _err_text(err, getter_name):
    try:
        v = getattr(err, getter_name)()
        return v if v is not None else ""
    except Exception:
        return ""


def collect_unfixable_edtf(errors):
    """Pull EDTF values from validator errors in the
    "[ohm] Invalid date - *_date:edtf; unfixable" group.

    Returns a list of strings (the offending EDTF values), in the order
    first seen, deduplicated.
    """
    seen = set()
    out = []
    for err in errors:
        title = _err_text(err, "getMessage")
        if UNFIXABLE_EDTF_TITLE_MARKER not in title.lower():
            continue

        # Try the description first (per-instance detail; what's shown
        # next to each child node in the panel tree).
        value = None
        detail = _err_text(err, "getDescription")
        if detail:
            # Strip a leading "key=" / "key: " if present, keep the value.
            # Examples we expect to see:
            #   "start_date:edtf=1856..1900~"
            #   "end_date:edtf: ~1856"
            #   "1856..1900~"
            value = _extract_edtf_value(detail)

        # Fallback: read the tag off the affected primitive(s).
        if not value:
            try:
                prims = list(err.getPrimitives())
            except Exception:
                prims = []
            for prim in prims:
                for key in EDTF_KEYS:
                    v = prim.get(key)
                    if v:
                        value = v
                        break
                if value:
                    break

        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


_NOT_VALID_EDTF_RE = re.compile(
    r"\s+is\s+not\s+valid\s+EDTF\b", re.IGNORECASE
)


def _extract_edtf_value(detail):
    """Pull just the EDTF value out of a validator description string.

    The validator's unfixable-EDTF description is of the form:
        <value> is not valid EDTF and cannot be normalized. Manual review needed.

    Returns just <value>, stripped. Returns None if the sentinel phrase
    isn't present.
    """
    if not detail:
        return None
    m = _NOT_VALID_EDTF_RE.search(detail)
    if not m:
        return None
    value = detail[:m.start()].strip()
    return value or None


def write_unfixable_edtf_file(base_path, counter, values):
    """Write one EDTF value per line to "<base_path>.unfixable_edtf.NNN".

    Uses the same counter as the remainder file so they pair up.
    """
    out_path = "%s.unfixable_edtf.%0*d" % (base_path, COUNTER_WIDTH, counter)
    f = open(out_path, "w")
    try:
        for val in values:
            f.write(val.encode("UTF-8") if isinstance(val, unicode) else val)
            f.write("\n")
    finally:
        f.close()
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    path = prompt_for_path()
    if not path:
        return
    if not os.path.isfile(path):
        error("File not found:\n%s" % path)
        return

    try:
        lines = read_input(path)
    except Exception as e:
        error("Could not read file:\n%s\n\n%s" % (path, e))
        return

    entries, parse_errors = parse_lines(lines)
    if not entries:
        msg = "No valid object IDs found in file."
        if parse_errors:
            msg += "\n\nFirst few unparseable lines:\n" + "\n".join(
                "  line %d: %s" % (ln, txt) for ln, txt in parse_errors[:5]
            )
        warn(msg)
        return

    batch_size = prompt_for_batch_size()
    if batch_size is None:
        return

    batch = entries[:batch_size]
    consumed_indices = [orig_idx for _, _, orig_idx in batch]

    # Resolve base path and next counter for output naming.
    base_path, _existing_n = strip_counter_suffix(path)
    counter = next_counter(base_path)

    query = build_overpass_query(batch)

    try:
        ds = download_via_josm(query)
    except Exception as e:
        error("Overpass download failed:\n%s" % e)
        return

    if ds is None or ds.allPrimitives().isEmpty():
        warn("Overpass returned no objects.")
        try:
            write_remainder(base_path, counter, lines, consumed_indices)
        except Exception:
            pass
        return

    layer_name = "Batch validate (%d objs from %s)" % (
        len(batch), os.path.basename(path)
    )
    layer = OsmDataLayer(ds, layer_name, None)
    MainApplication.getLayerManager().addLayer(layer)

    try:
        out_path = write_remainder(base_path, counter, lines, consumed_indices)
    except Exception as e:
        warn("Validation will run, but remainder file write failed:\n%s" % e)
        out_path = None

    primitive_count = ds.allPrimitives().size()

    def after_validation(validator_errors):
        # Diagnostic counts for visibility when the unfixable file is empty.
        n_total = len(validator_errors)
        n_any_unfixable = 0
        n_target_group = 0  # [ohm] Invalid date - *_date:edtf; unfixable
        for err in validator_errors:
            try:
                t = (err.getMessage() or "")
            except Exception:
                t = ""
            tl = t.lower()
            if "unfixable" in tl:
                n_any_unfixable += 1
            if UNFIXABLE_EDTF_TITLE_MARKER in tl:
                n_target_group += 1

        unfixable_rows = collect_unfixable_edtf(validator_errors)
        unfixable_path = None
        if unfixable_rows:
            try:
                unfixable_path = write_unfixable_edtf_file(
                    base_path, counter, unfixable_rows
                )
            except Exception as e:
                warn("Unfixable EDTF file write failed:\n%s" % e)

        summary = [
            "Requested: %d IDs (first %d of %d valid entries)"
            % (len(batch), len(batch), len(entries)),
            "Loaded into layer: %d primitives" % primitive_count,
            "Validator errors total: %d" % n_total,
            "  ... any 'unfixable' group: %d" % n_any_unfixable,
            "  ... '*_date:edtf; unfixable' group: %d" % n_target_group,
            "EDTF values written: %d" % len(unfixable_rows),
        ]
        if out_path:
            summary.append("Remainder written to:\n  %s" % out_path)
        if unfixable_path:
            summary.append(
                "Unfixable EDTF values written to:\n  %s" % unfixable_path
            )
        elif n_target_group > 0:
            summary.append(
                "(Group found but value extraction failed -- "
                "check error description format.)"
            )
        if parse_errors:
            summary.append(
                "Skipped %d unparseable line(s) (preserved in remainder)."
                % len(parse_errors)
            )
        summary.append("Validator results are in the Validation panel.")
        info("\n".join(summary))

    run_validation_async(layer, after_validation)


main()
