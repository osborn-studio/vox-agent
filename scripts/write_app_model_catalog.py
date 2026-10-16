"""Embed catalog identity from the same Swift source compiled by the App build."""
import argparse
import json
from pathlib import Path
import re


def write_catalog(source: Path, app: Path):
    text = source.read_text()
    catalog = {}
    for swift, key in [('modelID', 'model_id'), ('revision', 'revision'), ('digest', 'digest')]:
        values = re.findall(r'static let ' + swift + r' = "([A-Za-z0-9.-]+)"', text)
        if len(values) != 1:
            raise ValueError('Expected one literal compiled catalog identity')
        catalog[key] = values[0]
    destination = app / 'Contents/Resources/QWEN_MODEL_CATALOG.json'
    with destination.open('x') as output:
        json.dump(catalog, output, sort_keys=True, indent=2)
        output.write('\n')
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog-source', required=True, type=Path)
    parser.add_argument('--app', required=True, type=Path)
    args = parser.parse_args()
    write_catalog(args.catalog_source, args.app)
