"""The flight_recorder HDF5 layout (format version 1). The Python and C++ writers both produce exactly this.

    /                               attrs: format="flight_recorder", format_version=1, writer="python"|"cpp",
    |                                      created (ISO 8601), host, + user metadata (str / int / float / arrays)
    +-- streams/
    |   +-- <stream>/               attrs: columns (names, in order), n_rows
    |       +-- <column>            float64, shape (n_rows,), chunked, gzip + shuffle, resizable
    +-- records/
    |   +-- <group>/
    |       +-- <key>/              attrs: scalars given to record(...)
    |           +-- <name>          any-shape float64 array given to record(...)
    +-- events/
        +-- time                    float64 (n_events,)
        +-- kind                    variable-length UTF-8 string
        +-- detail                  variable-length UTF-8 string

Streams are stored column by column so that one column can be read without touching the others, and so that
pandas.DataFrame({name: column}) is a zero-copy view per column. Everything numeric is float64.
"""

FORMAT_NAME = 'flight_recorder'
FORMAT_VERSION = 1

STREAMS = 'streams'
RECORDS = 'records'
EVENTS = 'events'

HDF5_EXTENSIONS = ('.h5', '.hdf5', '.hdf', '.he5')


def h5_path(path: str) -> str:
    """Keep a path that already has an HDF5 extension; otherwise append '.h5' (never drop part of the name)."""
    import os
    return path if os.path.splitext(path)[1].lower() in HDF5_EXTENSIONS else path + '.h5'


CHUNK_ROWS = 4096          # rows per HDF5 chunk for stream columns (32 KiB of float64)
GZIP_LEVEL = 4             # 1 = fastest, 9 = smallest; 4 is a good trade-off for sensor data
