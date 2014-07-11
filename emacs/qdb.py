#!/usr/bin/env python
from __future__ import print_function

import argparse
from cmd import Cmd
import json
from multiprocessing import Process

from websocket import create_connection

DEFAULT_WS_ADDRESS = 'ws://localhost:8002/debug_session/{uuid}'
DEFAULT_UUID = 'qdb'

# The format of the outfile
FILE_FORMAT = '/tmp/qdb/.%s'

_version = '0.1.0.0'


class QdbRepl(Cmd, object):
    def __init__(self,
                 wsaddress=None,
                 auth_msg=None,
                 uuid=None,
                 prompt='(qdb) '):
        self.auth_msg = auth_msg
        self.printer = None
        self.prompt = prompt
        self.wsaddress = wsaddress
        self.uuid = uuid
        self._ws = None
        self._stop = False
        super(QdbRepl, self).__init__()

    def _connect(self):
        """
        Connects to the server and sends start.
        """
        self._ws = create_connection(self.wsaddress.format(uuid=self.uuid))
        self.send_command('start', self.auth_msg)
        self.filename = FILE_FORMAT % self.uuid
        self.printer = Process(
            target=ServerPrinter,
            args=(self._ws, self.filename),
        )
        self.printer.start()
        print('qdb connected: ' + self.filename)

    def fmt_msg(self, event, payload):
        """
        Formats a message to send to the server.
        """
        if not payload:
            return json.dumps({'e': event})
        return json.dumps({
            'e': event,
            'p': payload,
        })

    def send_command(self, event, payload=None):
        """
        Sends a formatted command.
        """
        self.send(self.fmt_msg(event, payload))

    def send(self, msg):
        """
        Sends a message to the server.
        """
        self._ws.send(msg)

    def preloop(self):
        """
        Prompts for any missing fields.
        """
        if not self.wsaddress:
            self.wsaddress = DEFAULT_WS_ADDRESS
        if not self.uuid:
            self.uuid = DEFAULT_UUID
        if not self.auth_msg:
            self.auth_msg = ''
        self._connect()

    def postcmd(self, stop, line):
        """
        Post command hook that allows us to stop the loop.
        """
        return stop or self._stop

    def parse_break_arg(self, arg):
        """
        Parses a breakpoint payload out of an argument.
        """
        args = arg.split()
        if len(args) == 1:
            print('*** error: break_arg: Missing line number')
        file_ = args[0]
        line = args[1]
        func = None
        cond = None
        for pair in zip([func, cond], args[2:]):
            pair[0] = pair[1]
        return {
            'file': file_,
            'line': line,
            'func': func,
            'cond': cond,
        }

    def missing_argument(self, cmd):
        print('*** error: %s: missing argument(s)' % cmd)

    def default(self, line):
        """
        eval if no command is given.
        """
        self.send_command('eval', line)

    def do_step(self, arg):
        self.send_command('step')
    do_s = do_step

    def do_return(self, arg):
        self.send_command('return')
    do_r = do_return

    def do_next(self, arg):
        self.send_command('next')
    do_n = do_next

    def do_until(self, arg):
        self.send_command('until')
    do_unt = do_until

    def do_continue(self, arg):
        self.send_command('continue')
    do_c = do_continue

    def do_watch(self, arg):
        if not arg:
            self.missing_argument('w(atch)')
            return
        self.send_command('set_watch', arg.split())
    do_w = do_watch

    def do_unwatch(self, arg):
        if not arg:
            self.missing_argument('unw(atch)')
            return
        self.send_command('clear_watch', arg.split())
    do_unw = do_unwatch

    def do_break(self, arg, temp=False):
        if not arg:
            self.missing_argument('b(reak)')
            return
        break_arg = self.parse_break_arg(arg, temp)
        if break_arg:
            self.send_command('set_break', break_arg)
    do_b = do_break

    def do_clear(self, arg):
        if not arg:
            self.missing_argument('cl(ear)')
            return
        break_arg = self.parse_break_arg(arg)
        if break_arg:
            self.send_command('clear_break', break_arg)
    do_cl = do_clear

    def do_tbreak(self, arg):
        self.do_break(arg, temp=True)

    def do_list(self, arg):
        if not arg:
            self.missing_argument('l(ist)')
            return
        args = arg.split()
        file_ = args[0]
        start = None
        end = None
        for pair in zip([start, end], args[1:]):
            pair[0] = pair[1]
        self.send_command('list', {
            'file': file_,
            'start': start,
            'end': end,
        })
    do_l = do_list

    def do_quit(self, arg):
        if not arg or arg == 'soft':
            self.send_command('disable', 'soft')
        elif arg == 'hard':
            self.send_command('disable', 'hard')
        else:
            print('*** error: disable: argument must be \'soft\' or \'hard\'')
        self._stop = True  # Mark that we should exit the cmdloop.
    do_q = do_quit
    do_EOF = do_quit  # EOF soft kills.


class ServerPrinter(object):
    """
    Manages reading from the server and pretty printing the outstream.
    """
    def __init__(self, socket, filename):
        self.socket = socket
        self.filename = filename
        self._file = open(filename, 'w', 0)
        self.writeln('Tracing...')
        for event in self.get_events():
            evfn = getattr(self, 'event_' + event['e'], None)
            if not evfn:
                self.unknown_event(event['e'])
            else:
                evfn(event.get('p'))

    def get_events(self):
        """
        Yields unpacked events from the socket.
        """
        while True:
            try:
                yield json.loads(self.socket.recv())
            except:
                return

    def writeln(self, msg):
        print(msg, file=self._file)

    def unknown_event(self, e):
        self.writeln('*** error: %s: unknown event type' % e)

    def event_print(self, payload):
        out = payload['output']
        if out:
            self.writeln(payload['input'] + ': ' + out)

    def event_list(self, payload):
        self.writeln(payload)

    def event_stack(self, payload):
        frame = payload[-1]
        self.writeln('> %s:%d' % (frame['file'], frame['line']))
        self.writeln('-> ' + frame['code'])

    def event_watchlist(self, payload):
        self.writeln('watchlist: [')
        for watched in payload:
            self.writeln('  > %s: %s' % (watched['name'], watched['value']))
        self.writeln(']')

    def event_breakpoints(self, payload):
        """
        Ignore these.
        """
        pass

    def event_error(self, payload):
        self.writeln('*** error: %s: %s' % (payload['type'], payload['data']))

    def event_return(self, payload):
        self.writeln('--> returning with %s' % payload)

if __name__ == '__main__':
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        '-w', '--ws-address',
        type=str,
        metavar='ADDR-FMT',
        help='The websocket address format string. containing {{uuid}}',
    )
    argparser.add_argument(
        '-u', '--uuid',
        type=str,
        metavar='UUID',
        help='The session uuid that you wish to connect to.'
    )
    argparser.add_argument(
        '-a', '--auth-msg',
        type=str,
        metavar='AUTH-MSG',
        help='The authentication message to send with the start event.'
    )
    args = argparser.parse_args()
    repl = QdbRepl()
    repl.wsaddress = args.ws_address
    repl.uuid = args.uuid
    repl.auth_msg = args.auth_msg
    repl.cmdloop()
    os.remove(repl.filename)  # Cleans up the tmp output file.