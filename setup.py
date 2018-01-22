# -*- coding: utf-8 -*-
from setuptools import setup, find_packages


def parse_requirements(req_file):
    """Parse a requirements.txt file to a list of requirements"""
    with open(req_file, 'r') as fb:
        reqs = [
            req for req in fb.readlines()
            if req.strip() and not req.startswith('#')
        ]
    return list(reqs)


setup_requires = (
    'pytest-runner',
    )
install_requires = parse_requirements('requirements/main.txt')
tests_require = parse_requirements('requirements/test.txt')
extras_require = {
    'test': tests_require,
    }
description = "Connexions Nebu publishing utility"
with open('README.rst', 'r') as readme:
    long_description = readme.read()

setup(
    name='nebuchadnezzar',
    version='1.0.0',
    author='Connexions team',
    author_email='info@cnx.org',
    url="https://github.com/connexions/nebuchadnezzar",
    license='AGPL, See also LICENSE.txt',
    description=description,
    long_description=long_description,
    setup_requires=setup_requires,
    install_requires=install_requires,
    tests_require=tests_require,
    extras_require=extras_require,
    test_suite='nebu.tests',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'nebu.tests': ['data/**/*.*'],
        },
    entry_points="""\
    [console_scripts]
    neb = nebu.cli.main:cli
    """,
    )