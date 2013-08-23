uvwsgi: a Python WSGI server
============================

uvwsgi is a Python WSGI server whhich uses *libuv* and *http-parser* libraries
also used in `Node.JS <https://github.com/joyent/node>`_ through their Python binding libraries:

* `pyuv <https://github.com/saghul/pyuv>`_
* `http-parser <https://github.com/benoitc/http-parser>`_

It's still work in progress.

Example usage::

    from flask import Flask
    from uvwsgi import run

    app = Flask(__name__)

    @app.route('/')
    def index():
        return 'hello world!'

    run(app, ('0.0.0.0', 8088))

The ``uvwsgi-run`` command line application can also be used to serve WSGI applications
directly. Assuming the code above this lines is stored in a file called `tst.py`, it can be
served as follows::

    uvwsgi-run --app tst:app --port 8888


