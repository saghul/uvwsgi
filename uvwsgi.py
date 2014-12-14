
import logging
import os
import pyuv
import signal
import sys
import time
import traceback

try:
    from io import BytesIO                      # python3
except ImportError:
    from cStringIO import StringIO as BytesIO   # python2

from http_parser.parser import HttpParser

__all__ = ['run']


__version__ = '0.3.2'

logger = logging.getLogger('uvwsgi')
logger.setLevel(logging.DEBUG)
logging.basicConfig()

DEBUG = os.getenv('DEBUG', None) is not None


# Python 2/3 compatibility stuff
PY2 = sys.version_info[0] == 2
if PY2:
    bytes_type = str
    unicode_type = unicode

    exec("""def reraise(tp, value, tb=None): raise tp, value, tb""")

else:
    bytes_type = bytes
    unicode_type = str

    def reraise(tp, value, tb=None):
        if value.__traceback__ is not tb:
            raise value.with_traceback(tb)
        raise value


def wsgi_to_bytes(s):
    if isinstance(s, bytes_type):
        return s
    assert isinstance(s, unicode_type)
    return s.encode('iso-8859-1')


_WEEKDAYNAME = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHNAME = [None,  # Dummy so we can use 1-based month numbers
              "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def date_time_string(timestamp=None):
    if timestamp is None:
        timestamp = time.time()
    year, month, day, hh, mm, ss, wd, _y, _z = time.gmtime(timestamp)
    return "%s, %02d %3s %4d %02d:%02d:%02d GMT" % (
            _WEEKDAYNAME[wd], day, _MONTHNAME[month], year, hh, mm, ss)


class ErrorStream(object):

    def __init__(self, logger):
        self._logger = logger

    def write(self, data):
        self._logger.error(data)

    def writelines(self, seq):
        for item in seq:
            self.write(item)

    def flush(self):
        pass


class HTTPRequest(object):

    def __init__(self, connection):
        self.connection = connection
        self.headers = None
        self.body = None
        self.method = None
        self.url = None
        self.protocol_version = None
        self.close_connection = False

    def process_data(self, data):
        parser = self.connection.parser
        ndata = len(data)
        parsed = parser.execute(data, ndata)
        if ndata != parsed:
            logger.error('Parsing HTTP request')
            self.connection.close()
            return
        if parser.is_headers_complete() and not self.headers:
            self.headers = parser.get_headers()
            self.method = parser.get_method()
            self.url = parser.get_url()
            self.protocol_version = 'HTTP/%s' % '.'.join(map(str, parser.get_version()))
            self.close_connection = not parser.should_keep_alive()
        if parser.is_message_complete():
            # Do the recv_body late, this way the HttpParser will have all the chunks
            # in a list and we only join them once, at the end
            self.body = BytesIO(parser.recv_body())
            self.run_wsgi()

    def run_wsgi(self):
        headers_set = []
        headers_sent = []

        def write(data):
            if not headers_set:
                raise AssertionError("write() before start_response()")
            elif not headers_sent:
                # Before the first output, send the stored headers
                buf = []
                status, response_headers = headers_sent[:] = headers_set
                code, _, msg = status.partition(' ')
                buf.append(wsgi_to_bytes('%s %d %s\r\n' % (self.protocol_version, int(code), msg)))
                header_keys = set()
                for key, value in response_headers:
                    buf.append(wsgi_to_bytes('%s: %s\r\n' % (key, value)))
                    header_keys.add(key.lower())
                if 'content-length' not in header_keys:
                    self.close_connection = True
                if 'server' not in header_keys:
                    buf.append(wsgi_to_bytes('Server: uvwsgi/%s\r\n' % __version__))
                if 'date' not in header_keys:
                    buf.append(wsgi_to_bytes('Date: %s\r\n' % date_time_string()))
                buf.append(b'\r\n')
                self.connection.write(b''.join(buf))

            assert type(data) is bytes, 'applications must write bytes'
            self.connection.write(data)

        def start_response(status, response_headers, exc_info=None):
            if exc_info:
                try:
                    if headers_sent:
                        reraise(*exc_info)
                finally:
                    exc_info = None
            elif headers_set:
                raise AssertionError("Headers already set!")

            headers_set[:] = [status, response_headers]
            # TODO: check hop by hop headers here?
            return write

        # Prepare WSGI environment
        env = self.connection.parser.get_wsgi_environ()
        env['wsgi.version'] = (1, 0)
        env['wsgi.url_scheme'] = 'http'
        env['wsgi.input'] = self.body
        env['wsgi.errors'] = ErrorStream(logger)
        env['wsgi.multithread'] = False
        env['wsgi.multiprocess'] = False
        env['wsgi.run_once'] = False

        # Run WSGI application
        app = self.connection.server.app
        try:
            app_response = app(env, start_response)
        except Exception:
            logger.exception('Running WSGI application')
            if DEBUG:
                response_body = traceback.format_exc()
                response_headers = [('Content-Type', 'text/plain'),
                                    ('Content-Length', str(len(response_body)))]
                start_response('500 Internal Server Error', response_headers, exc_info=sys.exc_info())
                app_response = [response_body]
            else:
                self.connection.finish()
                return
        try:
            for data in app_response:
                write(data)
            if not headers_sent:
                write(b'')
        except Exception:
            logger.exception('Running WSGI application')
        finally:
            if hasattr(app_response, 'close'):
                app_response.close()
        self.end()
        if DEBUG:
            status = headers_set[0]
            self._log(int(status.split()[0]))

    def end(self):
        if self.close_connection:
            self.connection.finish()
        else:
            self.connection.request = None

    def _log(self, code):
        if code < 400:
            log_method = logger.info
        elif code < 500:
            log_method = logger.warning
        else:
            log_method = logger.error
        log_method('%d %s %s %s', code, self.method, self.url, self.connection.remote_address)


class HTTPConnection(object):

    def __init__(self, handle, server):
        self.server = server
        self.request = None
        self.parser = HttpParser(kind=0)    # request only parser

        self._handle = handle
        self._handle.start_read(self._on_read)
        self._closed = False
        self._must_close = False
        self._pending_writes = 0
        self._remote_address = self._handle.getpeername()

    @property
    def remote_address(self):
        return self._remote_address

    def write(self, data):
        self._handle.write(data, self._on_write)
        self._pending_writes += 1

    def finish(self):
        """Close connection once all pending writes are done"""
        if self._closed or self._must_close:
            return
        if self._pending_writes == 0:
            self.close()
        else:
            self._must_close = True

    def close(self):
        if self._closed:
            return
        if DEBUG:
            logger.debug('Connection from %s closed', self.remote_address)
        self._handle.close(self._on_close)
        self._closed = True

    def _on_read(self, handle, data, error):
        if error is not None:
            if DEBUG:
                if error == pyuv.errno.UV_EOF:
                    logger.debug('Client closed connection')
                else:
                    logger.debug('Read error: %d %s', error, pyuv.errno.strerror(error))
            self.close()
            return
        if self.request is None:
            self.request = HTTPRequest(self)
        self.request.process_data(data)

    def _on_close(self, handle):
        self.server.connections.remove(self)
        self._handle = self.server = self.request = self.parser = None

    def _on_write(self, handle, error):
        if DEBUG and error is not None:
            logger.error('Writing response: %d %s', error, pyuv.errno.strerror(error))
        self._pending_writes -= 1
        if self._pending_writes == 0 and self._must_close:
            self.close()


class WSGIServer(object):

    def __init__(self, loop, application, address, fd):
        self.app = application
        self.connections = set()
        self._loop = loop
        self._handle = pyuv.TCP(loop)
        if fd is None:
            self._handle.bind(address)
        else:
            self._handle.open(os.dup(fd))
        self._stopped = False
        logger.info('%s listening on %s', self.__class__.__name__, self._handle.getsockname())

    def start(self):
        self._handle.listen(self._on_connection)

    def stop(self):
        if self._stopped:
            return
        self._handle.close()
        for c in self.connections:
            c.close()
        self._loop.stop()
        self._stopped = True

    def _on_connection(self, handle, error):
        if error is not None:
            logger.error('Accepting incoming connection: %d %s', error, pyuv.errno.strerror(error))
            return
        conn = pyuv.TCP(self._handle.loop)
        self._handle.accept(conn)
        http_connection = HTTPConnection(conn, self)
        self.connections.add(http_connection)
        if DEBUG:
            logger.debug('Incoming connection from %s', conn.getpeername())


def run(application, address=None, fd=None):
    # The one and only event loop
    loop = pyuv.Loop.default_loop()

    # The one and only WSGI server
    server = WSGIServer(loop, application, address, fd)

    # Signal handlers for quitting
    sigint_h = pyuv.Signal(loop)
    sigint_h.start(lambda *x: server.stop(), signal.SIGINT)
    sigterm_h = pyuv.Signal(loop)
    sigterm_h.start(lambda *x: server.stop(), signal.SIGTERM)

    # Here we go!
    server.start()
    loop.run()

    # Free all resources
    for handle in loop.handles:
        if not handle.closed:
            handle.close()


def main():
    from optparse import OptionParser

    def import_app(s):
        sys.path.insert(0, os.path.abspath(os.curdir))
        mod, attr = s.rsplit(':', 1)
        module = __import__(mod)
        return getattr(module, attr)

    parser = OptionParser()
    parser.add_option('-i', '--interface', default='0.0.0.0', help='Interface to listen on for incoming requests')
    parser.add_option('-p', '--port', default='8088', help='Port to listen on for incoming requests')
    parser.add_option('-f', '--fd', default=None, help='File descriptor to listen on for requests')
    options, args = parser.parse_args()

    if len(args) != 1:
        raise RuntimeError('invalid arguments')

    app = import_app(args[0])
    if options.fd is None:
        interface = options.interface
        port = int(options.port)
        address = (interface, port)
        fd = None
    else:
        address = None
        fd = int(options.fd)

    run(app, address=address, fd=fd)


if __name__ == '__main__':
    main()
