# Written by Claude.ai
# Prompted by Jeff Meyer
# Please test this script on a limited set of chronologies before uploading to OHM

def process_chronology_relation(relation):
    if relation.get('type') != 'chronology':
        return

    min_start     = None
    min_start_raw = None
    max_end       = None
    max_end_raw   = None

    # Track the child with the maximum start_date separately
    max_start         = None
    max_start_raw     = None
    max_start_end_raw = None   # end_date of the child with the latest start_date

    for member in relation.getMembers():
        child = member.getMember()
        if not isinstance(child, Relation):
            continue

        start_raw = child.get('start_date')
        if start_raw:
            parsed = parse_date(start_raw)
            if parsed is not None:
                if min_start is None or parsed < min_start:
                    min_start     = parsed
                    min_start_raw = start_raw
                if max_start is None or parsed > max_start:
                    max_start         = parsed
                    max_start_raw     = start_raw
                    max_start_end_raw = child.get('end_date')  # may be None

        end_raw = child.get('end_date')
        if end_raw:
            parsed = parse_date(end_raw)
            if parsed is not None:
                if max_end is None or parsed > max_end:
                    max_end     = parsed
                    max_end_raw = end_raw

    # Apply start_date
    if min_start is not None:
        new_start = format_date(min_start_raw, min_start)
        relation.put('start_date', new_start)
        print("  start_date → {}".format(new_start))
    else:
        print("  No start_date found on any child; leaving parent unchanged.")

    # Apply end_date: open-ended only if the latest-starting child has no end_date
    if max_start is not None and max_start_end_raw is None:
        # Most recent child is open-ended → chronology is open-ended
        if relation.get('end_date') is not None:
            relation.remove('end_date')
            print("  end_date removed (latest child is open-ended / current)")
        else:
            print("  end_date already absent (open-ended)")
    elif max_end is not None:
        new_end = format_date(max_end_raw, max_end)
        relation.put('end_date', new_end)
        print("  end_date → {}".format(new_end))
    else:
        print("  No end_date found on any child; leaving parent unchanged.")
