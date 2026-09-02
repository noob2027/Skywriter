"""Module entry point for ``python -m skywriter``."""

import sys

if __name__ == "__main__":
    from skywriter.packaged_runtime_smoke import PACKAGED_SERIAL_IMPORT_SMOKE_ARGUMENT

    if PACKAGED_SERIAL_IMPORT_SMOKE_ARGUMENT in sys.argv:
        from skywriter.packaged_runtime_smoke import run_packaged_serial_import_smoke

        raise SystemExit(run_packaged_serial_import_smoke(sys.argv))

    from skywriter.main import main

    raise SystemExit(main())
