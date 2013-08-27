uvwsgi: a Python WSGI server
============================

uvwsgi is a Python WSGI server whhich uses *libuv* and *http-parser* libraries
also used in `Node.JS <https://github.com/joyent/node>`_ through their Python binding libraries:

* `pyuv <https://github.com/saghul/pyuv>`_
* `http-parser <https://github.com/benoitc/http-parser>`_


Motivalion
----------

There are  abunch of great WSGI servers out there, so why create a new one? I've been
playing with Flask and WSGI lately and I wanted to see the guts of it. As you can see
the code is pretty short, I expect to make more changes and more features to it though.


Status
------

**uvwsgi should not be used in production.** It's still work in progress.


Installtion
-----------

uvwsgi can be easily installed with pip::

    pip install uvwsgi


Usage
-----

Example usage::

    from flask import Flask
    from uvwsgi import run

    app = Flask(__name__)

    @app.route('/')
    def index():
        return 'hello world!'

    run(app, ('0.0.0.0', 8088))

The ``uvwsgi`` command line application can also be used to serve WSGI applications
directly. Assuming the code above this lines is stored in a file called `tst.py`, it can be
served as follows::

    uvwsgi tst:app --port 8888

NOTE: You need to install the package first in order to have the ``uvwsgi`` command available.


Author
------

Saúl Ibarra Corretgé <saghul@gmail.com>


License
-------

Unless stated otherwise on-file uvwsgi uses the MIT license, check LICENSE file.

