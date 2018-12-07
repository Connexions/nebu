import os
import sys
import tempfile
import zipfile
from pathlib import Path

import click
import requests
from requests.auth import HTTPBasicAuth
from litezip import (
    parse_collection,
    parse_module,
    Collection,
)

from ._common import common_params, get_base_url, logger
from .validate import is_valid


def parse_book_tree(bookdir):
    """Converts filesystem booktree back to a struct"""
    struct = []
    for dirname, subdirs, filenames in os.walk(str(bookdir)):
        if 'collection.xml' in filenames:
            struct.append(parse_collection(Path(dirname)))
        elif 'index.cnxml' in filenames:
            struct.append(parse_module(Path(dirname)))
    return struct


def _push(base_url, struct, username, password):
    auth = HTTPBasicAuth(username, password)

    """Check for good credentials"""
    auth_ping_url = '{}/api/auth-ping'.format(base_url)
    auth_ping_resp = requests.post(auth_ping_url, auth=auth)

    if auth_ping_resp.status_code == 401:
        logger.debug('Temporary raw output...')
        logger.error('Bad credentials: \n{}'.format(auth_ping_resp.content))
        return False

    """Check for permission to push"""
    push_ping_url = '{}/api/push-ping'.format(base_url)
    push_ping_resp = requests.post(push_ping_url, auth=auth)

    if push_ping_resp.status_code == 401:
        logger.debug('Temporary raw output...')
        logger.error('Pushing not allowed: \n{}'.format(
            push_ping_resp.content))
        return False

    """Push the struct to a repository"""
    collection_id = struct[0].id
    # Base encapsulating directory within the zipfile
    base_file_path = Path(collection_id)

    # Zip it up!
    # TODO Move this block of logic to litezip. Maybe?
    _, zip_file = tempfile.mkstemp()
    zip_file = Path(zip_file)
    with zipfile.ZipFile(str(zip_file), 'w') as zb:
        for model in struct:
            files = []
            # Write the content file into the zip.
            if isinstance(model, Collection):
                file = model.file
                rel_file_path = base_file_path / model.file.name
                files.append((file, rel_file_path))
                for resource in model.resources:
                    files.append((resource.data,
                                  base_file_path / resource.filename))
            else:  # Module
                file = model.file
                rel_file_path = base_file_path / model.id / model.file.name
                files.append((file, rel_file_path))
                for resource in model.resources:
                    files.append((resource.data,
                                  base_file_path /
                                  model.id / resource.filename))

            for file, rel_file_path in files:
                zb.write(str(file), str(rel_file_path))

    url = '{}/api/push-litezip'.format(base_url)
    headers = {'X-API-Version': '4'}
    data = {'publisher': username}

    files = {
        'file': ('contents.zip', zip_file.open('rb'),),
    }
    # Send it!
    resp = requests.post(url, data=data, files=files,
                         auth=auth, headers=headers)

    # Clean up!
    zip_file.unlink()

    # Process any response messages
    if resp.status_code == 200:
        # TODO Nicely format this stuff. Wait for the Bravado client
        #      implementation to work with models to make this work easier.
        logger.debug('Temporary raw output...')
        from pprint import pformat
        logger.info('Publishing response: \n{}'
                    .format(pformat(resp.json())))
    elif resp.status_code == 400:
        # This way be Errors
        # TODO Nicely format this stuff. Wait for the Bravado client
        #      implementation to work with models to make this work easier.
        logger.debug('Temporary raw output...')
        logger.error('ERROR: \n{}'.format(resp.content))
        return False
    elif resp.status_code == 401:
        logger.debug('Temporary raw output...')
        logger.error('Bad credentials: \n{}'.format(resp.content))
        return False
    else:  # pragma: no cover
        logger.error("unknown response:\n  status: {}\n  contents: {}"
                     .format(resp.status_code, resp.text))
        raise RuntimeError('unknown response, see output above')

    return True


@click.command()
@common_params
@click.argument('env')
@click.argument('content_dir',
                type=click.Path(exists=True, file_okay=False))
@click.option('-u', '--username', type=str, prompt=True)
@click.option('-p', '--password', type=str, prompt=True, hide_input=True)
@click.option('-s', '--skip-validation', is_flag=True)
@click.pass_context
def push(ctx, env, content_dir, username, password,
         skip_validation):
    base_url = get_base_url(ctx, env)

    content_dir = Path(content_dir).resolve()
    struct = parse_book_tree(content_dir)

    if not skip_validation and not is_valid(struct):
        logger.info("We've got problems... :(")
        sys.exit(1)

    has_pushed = _push(base_url, struct, username, password)
    if has_pushed:
        logger.info("Great work!!! =D")
    else:
        logger.info("Stop the Press!!! =()")
        sys.exit(1)