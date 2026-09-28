"""Command line: inspect and export flight_recorder files.

    python3 -m flight_recorder info flight.h5
    python3 -m flight_recorder csv flight.h5 ticks -o ticks.csv
    python3 -m flight_recorder ros2logger-csv flight.h5 ticks -o legacy.csv
"""
import argparse
import os
import sys

from .csv_export import stream_to_csv, stream_to_ros2logger_csv
from .reader import FlightLog


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog='flight_recorder', description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    i = sub.add_parser('info', help='summary of streams, records, events and metadata')
    i.add_argument('path')
    for name, help_ in (('csv', 'plain CSV of one stream'),
                        ('ros2logger-csv', "one stream in ROS2Logger's CSV layout (time, x, y, z, yaw first)")):
        c = sub.add_parser(name, help=help_)
        c.add_argument('path')
        c.add_argument('stream')
        c.add_argument('-o', '--output', help='output CSV (default: <file>_<stream>.csv)')
    args = p.parse_args(argv)

    with FlightLog(args.path) as log:
        if args.cmd == 'info':
            print(log.summary())
            return 0
        out = args.output or f'{os.path.splitext(args.path)[0]}_{args.stream}.csv'
        (stream_to_csv if args.cmd == 'csv' else stream_to_ros2logger_csv)(log, args.stream, out)
        print(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
