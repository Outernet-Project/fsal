###########################################################################

import os
import sys
import atexit
import argparse

import fsal.server


def cleanup(pidfile):
    try:
        os.remove(pidfile)
    except OSError as e:
        print >>sys.stderr, "pidfile cleanup failed with error: %s" % str(e)


def main():
    # do the UNIX double-fork magic, see Stevens' "Advanced
    # Programming in the UNIX Environment" for details (ISBN 0201563177)
    try:
        pid = os.fork()
        if pid > 0:
            # exit first parent
            sys.exit(0)
    except OSError as e:
        print >>sys.stderr, "fork #1 failed: %d (%s)" % (e.errno, e.strerror)
        sys.exit(1)

    # decouple from parent environment
    os.setsid()
    os.umask(0)

    # do second fork
    try:
        pid = os.fork()
        if pid > 0:
            # exit from second parent
            sys.exit(0)
    except OSError as e:
        print >>sys.stderr, "fork #2 failed: %d (%s)" % (e.errno, e.strerror)
        sys.exit(1)

    parser = argparse.ArgumentParser(description='Daemonize FSAL server')
    parser.add_argument('--pid-file', metavar='PATH', help='Path for pid file',
                        default='./fsal-daemon.pid', dest='pidfile',
                        required=True
                        )
    args, unknown = parser.parse_known_args()

    pid = str(os.getpid())
    with open(args.pidfile, "w") as f:
        f.write("%s\n" % pid)

    atexit.register(lambda: cleanup(args.pidfile))

    fsal.server.main()


if __name__ == "__main__":
    main()
