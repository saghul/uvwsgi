
import logging
import os
import pyuv
import signal
import sys

from http_parser.parser import HttpParser

try:
    from io import BytesIO                      # python3
except ImportError:
    from cStringIO import StringIO as BytesIO   # python2

__all__ = ['run']


__version__ = '0.1.0'

logger = logging.getLogger('uvwsgi')
logger.setLevel(logging.DEBUG)
logging.basicConfig()

DEBUG = os.getenv('DEBUG', None) is not None


# Python 2/3 compatibility stuff
PY2 = sys.version_info[0] == 2
if PY2:
    bytes_type = str
    unicode_type = unicode
else:
    bytes_type = bytes
    unicode_type = str


def utf8(value):
    """Converts a string argument to a byte string.

    If the argument is already a byte string, it is returned unchanged.
    Otherwise it must be a unicode string and is encoded as utf8.
    """
    if isinstance(value, bytes_type):
        return value
    assert isinstance(value, unicode_type)
    return value.encode('utf-8')


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
        self.should_keep_alive = False

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
        if parser.is_message_complete():
            self.body = BytesIO(parser.recv_body())
            self.should_keep_alive = parser.should_keep_alive()
            self.run_wsgi()

    def run_wsgi(self):
        data = {}
        response = []

        def start_response(status, response_headers, exc_info=None):
            data['status'] = status
            data['headers'] = response_headers
            return response.append

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
        app_response = app(env, start_response)
        try:
            response.extend(app_response)
            body = b''.join(response)
        finally:
            if hasattr(app_response, 'close'):
                app_response.close()
        if not data:
            raise RuntimeError('WSGI application did not call start_response')

        status_code = int(data['status'].split()[0])
        headers = data['headers']
        header_set = set(k.lower() for (k, v) in headers)
        body = utf8(body)
        if status_code != 304:
            if 'content-length' not in header_set:
                headers.append(('Content-Length', str(len(body))))
            if 'content-type' not in header_set:
                headers.append(('Content-Type', 'text/html; charset=UTF-8'))
        if 'server' not in header_set:
            headers.append(('Server', 'uvwsgi/%s' % __version__))

        parts = [utf8('HTTP/1.1 ' + data['status'] + '\r\n')]
        for key, value in headers:
            parts.append(utf8(key) + b': ' + utf8(value) + b'\r\n')
        parts.append(b'\r\n')
        parts.append(body)
        self.connection.write(b''.join(parts))
        self.end()
        if DEBUG:
            self._log(status_code)

    def end(self):
        if not self.should_keep_alive:
            self_connection.finish()
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

    @property
    def remote_address(self):
        if not self._closed:
            return self._handle.getpeername()

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
            logger.debug('Connection from %s closed', self._handle.getpeername())
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
        if error is not None:
            logger.error('Writing response: %s', pyuv.errno.strerror(error))
        self._pending_writes -= 1
        if self._pending_writes == 0 and self._must_close:
            self.close()


class WSGIServer(object):

    def __init__(self, loop, application, address):
        self.app = application
        self.connections = set()
        self._handle = pyuv.TCP(loop)
        self._handle.bind(address)
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
        self._stopped = True

    def _on_connection(self, handle, error):
        if error is not None:
            logger.error('Accepting incoming connection: %s', pyuv.errno.strerror(error))
            return
        conn = pyuv.TCP(self._handle.loop)
        self._handle.accept(conn)
        http_connection = HTTPConnection(conn, self)
        self.connections.add(http_connection)
        if DEBUG:
            logger.debug('Incoming connection from %s', conn.getpeername())


def _close_loop(loop):
    def cb(handle):
        if not handle.closed:
            handle.close()
    loop.walk(cb)


def run(application, address):
    # The one and only event loop
    loop = pyuv.Loop.default_loop()

    # The one and only WSGI server
    server = WSGIServer(loop, application, address)

    # Signal handlers for quitting
    sigint_h = pyuv.Signal(loop)
    sigint_h.start(lambda *x: server.stop(), signal.SIGINT)
    sigint_h.unref()
    sigterm_h = pyuv.Signal(loop)
    sigterm_h.start(lambda *x: server.stop(), signal.SIGTERM)
    sigterm_h.unref()

    # Here we go!
    server.start()
    loop.run()

    # Free all resources
    _close_loop(loop)

