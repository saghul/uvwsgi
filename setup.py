# -*- coding: utf-8 -*-

import re
try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup


def get_version():
    return re.search(r"""__version__\s+=\s+(?P<quote>['"])(?P<version>.+?)(?P=quote)""", open('uvwsgi.py').read()).group('version')


setup(name             = "uvwsgi",
      version          = get_version(),
      author           = "Saúl Ibarra Corretgé",
      author_email     = "saghul@gmail.com",
      url              = "http://github.com/saghul/uvwsgi",
      description      = "Simple WSGI server using pyuv",
      long_description = open("README.rst").read(),
      install_requires = [i.strip() for i in open("requirements.txt").readlines() if i.strip()],
      py_modules       = ['uvwsgi'],
      zip_safe         = False,
      platforms        = ["POSIX", "Microsoft Windows"],
      entry_points     = {
                          'console_scripts': ['uvwsgi = uvwsgi:main']
                         },
      classifiers      = [
          "Development Status :: 4 - Beta",
          "Intended Audience :: Developers",
          "License :: OSI Approved :: MIT License",
          "Operating System :: POSIX",
          "Operating System :: Microsoft :: Windows",
          "Programming Language :: Python",
          "Programming Language :: Python :: 2",
          "Programming Language :: Python :: 2.6",
          "Programming Language :: Python :: 2.7",
          "Programming Language :: Python :: 3",
          "Programming Language :: Python :: 3.0",
          "Programming Language :: Python :: 3.1",
          "Programming Language :: Python :: 3.2",
          "Programming Language :: Python :: 3.3"
                         ]
)

