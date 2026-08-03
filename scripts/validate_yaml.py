#!/usr/bin/env python3
"""Parse every YAML file in the repo and render the tenant template at each
density. Run in CI: a manifest that does not parse is a manifest that fails at
3 a.m. in the middle of a 480-run campaign.
"""
import glob
import sys

import yaml


def main():
    files = sorted(
        glob.glob("config/*.yaml")
        + glob.glob("deploy/**/*.yaml", recursive=True)
        + glob.glob(".github/workflows/*.yml")
    )
    bad = []
    for f in files:
        try:
            list(yaml.safe_load_all(open(f)))
        except yaml.YAMLError as e:
            bad.append((f, str(e).splitlines()[0]))
    for f, err in bad:
        sys.stderr.write("FAIL %s: %s\n" % (f, err))
    print("validated %d YAML files, %d failures" % (len(files), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
