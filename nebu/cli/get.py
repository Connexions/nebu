import os

import click
import requests

from lxml import etree
from pathlib import Path

from ..logger import logger
from ._common import common_params, confirm, build_archive_url
from .exceptions import (MissingContent,
                         ExistingOutputDir,
                         OldContent,
                         )


@click.command()
@common_params
@click.option('-d', '--output-dir', type=click.Path(),
              help="output directory name (can't previously exist)")
@click.option('-t', '--book-tree', is_flag=True,
              help="create human-friendly book-tree")
@click.option('-r', '--get-resources', is_flag=True, default=False,
              help="Get all resources (images) in addition to source")
@click.argument('env')
@click.argument('col_id')
@click.argument('col_version')
@click.pass_context
def get(ctx, env, col_id, col_version, output_dir, book_tree, get_resources):
    """Retrieve the contents of a book, onto the local file system"""

    base_url = build_archive_url(ctx, env)

    version = None
    req_version = col_version
    if col_version.count('.') > 1:
        full_version = col_version.split('.')
        col_version = '.'.join(full_version[:2])
        version = '.'.join(full_version[1:])

    col_hash = '{}/{}'.format(col_id, col_version)
    # Fetch metadata
    url = '{}/content/{}'.format(base_url, col_hash)
    resp = requests.get(url)
    if resp.status_code >= 400:
        raise MissingContent(col_id, req_version)
    col_metadata = resp.json()
    if col_metadata['collated']:
        url = resp.url + '?as_collated=False'
        resp = requests.get(url)
        if resp.status_code >= 400:
            # This should never happen - indicates that only baked exists?
            raise MissingContent(col_id, req_version)
        col_metadata = resp.json()
    uuid = col_metadata['id']
    # metadata fetch used legacy IDs, so will only have
    # the latest minor version - if "version" is set, the
    # user requested an explicit minor (3 part version: 1.X.Y)
    # refetch metadata, using uuid and requested version
    if version and version != col_metadata['version']:
        url = '{}/contents/{}@{}'.format(base_url, uuid, version) + \
              '?as_collated=False'
        resp = requests.get(url)
        if resp.status_code >= 400:  # Requested version doesn't exist
            raise MissingContent(col_id, req_version)
        col_metadata = resp.json()

    version = col_metadata['version']

    # Generate full output dir as soon as we have the version
    if output_dir is None:
        output_dir = Path.cwd() / '{}_1.{}'.format(col_id, version)
    else:
        output_dir = Path(output_dir)
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir

    # ... and check if it's already been downloaded
    if output_dir.exists():
        raise ExistingOutputDir(output_dir)

    # Fetch extras (includes head and downloadable file info)
    url = '{}/extras/{}@{}'.format(base_url, uuid, version)
    resp = requests.get(url)

    # Latest defaults to successfully baked - we need headVersion
    if col_version == 'latest':
        version = resp.json()['headVersion']
        url = '{}/extras/{}@{}'.format(base_url, uuid, version)
        resp = requests.get(url)

    col_extras = resp.json()

    if version != col_extras['headVersion']:
        logger.warning("Fetching non-head version of {}."
                       "\n    Head: {},"
                       " requested {}".format(col_id,
                                              col_extras['headVersion'],
                                              version))
        if not(confirm("Fetch anyway? [y/n] ")):
            raise OldContent()

    # Write tree
    tree = col_metadata['tree']
    os.mkdir(str(output_dir))

    num_pages = _count_leaves(tree) + 1  # Num. of xml files to fetch
    try:
        label = 'Getting {}'.format(output_dir.relative_to(Path.cwd()))
    except ValueError:
        # Raised ONLY when output_dir is not a child of cwd
        label = 'Getting {}'.format(output_dir)
    with click.progressbar(length=num_pages,
                           label=label,
                           width=0,
                           show_pos=True) as pbar:
        _write_node(tree, base_url, output_dir, book_tree, get_resources, pbar)


def _count_leaves(node):
    if 'contents' in node:
        return sum([_count_leaves(child) for child in node['contents']])
    else:
        return 1


def _tree_depth(node):
    if 'contents' in node:
        return max([_tree_depth(child) for child in node['contents']]) + 1
    else:
        return 0


filename_by_type = {'application/vnd.org.cnx.collection': 'collection.xml',
                    'application/vnd.org.cnx.module': 'index.cnxml'}


def _safe_name(name):
    return name.replace('/', '∕').replace(':', '∶')


def gen_resources_sha1_cache(write_dir, resources):
    for resource in resources:
        with (write_dir / '.sha1sum').open('a') as s:
            # NOTE: the id is the sha1
            s.write('{}  {}\n'.format(resource['id'], resource['filename']))


def _write_node(node, base_url, out_dir, book_tree=False, get_resources=False,
                pbar=None, depth=None, pos={0: 0}, lvl=0):
    """Recursively write out contents of a book
       Arguments are:
        root of the json tree, archive url to fetch from, existing directory
       to write out to, format to write (book tree or flat) as well as a
       click progress bar, if desired. Depth is height of tree, used to reset
       the lowest level counter (pages) per chapter. All other levels (Chapter,
       unit) count up for entire book. Remaining args are used for recursion"""
    if depth is None:
        depth = _tree_depth(node)
        pos = {0: 0}
        lvl = 0
    if book_tree:
        #  HACK Prepending zero-filled numbers to folders to fix the sort order
        if lvl > 0:
            dirname = '{:02d} {}'.format(pos[lvl], _safe_name(node['title']))
        else:
            dirname = _safe_name(node['title'])  # book name gets no number

        out_dir = out_dir / dirname
        os.mkdir(str(out_dir))

    write_dir = out_dir  # Allows nesting only for book_tree case

    # Fetch and store the core file for each node
    resp = requests.get('{}/contents/{}'.format(base_url, node['id']))
    if resp:  # Subcollections cannot (yet) be fetched directly
        metadata = resp.json()
        resources = {r['filename']: r for r in metadata['resources']}
        filename = filename_by_type[metadata['mediaType']]
        url = '{}/resources/{}'.format(base_url, resources[filename]['id'])
        file_resp = requests.get(url)
        if not(book_tree) and filename == 'index.cnxml':
            write_dir = write_dir / metadata['legacy_id']
            os.mkdir(str(write_dir))
        filepath = write_dir / filename

        """Cache/store sha1-s for resources in a 'dot' file"""
        gen_resources_sha1_cache(write_dir, metadata['resources'])

        # core files are XML - this parse/serialize removes numeric entities
        filepath.write_bytes(etree.tostring(etree.XML(file_resp.text),
                                            encoding='utf-8'))
        if get_resources:
            for res in resources:  # Dict keyed by resource filename
                if res != filename:
                    filepath = write_dir / res
                    url = '{}/resources/{}'.format(base_url,
                                                   resources[res]['id'])
                    file_resp = requests.get(url)
                    filepath.write_bytes(file_resp.content)

        if pbar is not None:
            pbar.update(1)

    if 'contents' in node:  # Top-level or subcollection - recurse
        lvl += 1
        if lvl not in pos:
            pos[lvl] = 0
        if lvl == depth:  # Reset counter for bottom-most level: pages
            pos[lvl] = 0
        for child in node['contents']:
            #  HACK - the silly don't number Preface/Introduction logic
            if ((lvl == 1 and pos[1] == 0 and 'Preface' in child['title']) or
                    (pos[lvl] == 0 and child['title'] == 'Introduction')):
                pos[lvl] = 0
            else:
                pos[lvl] += 1
            _write_node(child, base_url, out_dir, book_tree, get_resources,
                        pbar, depth, pos, lvl)
