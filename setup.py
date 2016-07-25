# coding=UTF-8

import os
from setuptools import setup, find_packages
from codecs import open
from os import path


long_description = 'Raumfeld controlled by python scripts'
here = path.abspath(path.dirname(__file__))
s = path.join(here, 'README.txt')

if os.path.exists(s):
    long_description = open('README.txt').read()

setup(
    name='pyfeld',
    version='0.0.1a0',
    author='Jürgen Schwietering',
    author_email='scjurgen@yahoo.com',
    description='Raumfeld controlled by python scripts',
    long_description=long_description,
    license='MIT',
    keywords='raumfeld wlan-speakers loudspeakers upnp audio media',
    url='http://github.com/scjurgen/pyfeld',
    packages=['pyfeld', 'examples'],
    include_package_data=True,
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        'Topic :: Utilities',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
    ],
    py_modules=['pyfeld','DirBrowse'],
    # https://packaging.python.org/en/latest/requirements.html
    install_requires=['requests', 'readchar'],
    #    packages=find_packages(exclude=['contrib', 'docs', 'tests']),
    entry_points={
        'console_scripts': [
            'pyfeld=pyfeld.rfcmd:run_main'
        ],
    }
)
