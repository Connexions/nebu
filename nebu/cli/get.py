import os

import click
import requests

from lxml import etree
from pathlib import Path

from urllib.parse import urlparse, urlunparse

from ..logger import logger
from ._common import (common_params,
                      confirm,
                      get_base_url,
                      get_base_url_from_url,
                      )
from .exceptions import (MissingContent,
                         MissingBakedContent,
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
              help="Also get all resources (images)")
@click.option('-b', '--get_baked', is_flag=True, default=False,
              help="fetch baked version of content")
@click.argument('target', nargs=-1)
@click.pass_context
def get(ctx, target, output_dir, book_tree, get_resources, get_baked):
    """download and expand the completezip to the current working directory"""

    version = None
    if len(target) == 1:  # target as url
        base_url = get_base_url_from_url(target[0])
        content_path = urlparse(target[0]).path.split(':')[0]
        url = urlunparse(urlparse(base_url)._replace(path=content_path))
        col_version = None

    elif len(target) == 3:  # env colid ver
        env, col_id, col_version = target
        if col_version.count('.') > 1:
            full_version = col_version.split('.')
            col_version = '.'.join(full_version[:2])
            version = '.'.join(full_version[1:])
        else:
            version = None

        base_url = get_base_url_from_url(get_base_url(ctx, env))
        col_hash = '{}/{}'.format(col_id, col_version)
        url = '{}/content/{}'.format(base_url, col_hash)
    else:
        raise click.UsageError("Wrong number of arguments", ctx=ctx)

    # Fetch metadata
    resp = requests.get(url)
    if resp.status_code >= 400:
        raise MissingContent(url)
    col_metadata = resp.json()

    if get_baked and not col_metadata['collated']:
        raise MissingBakedContent(target)

    uuid = col_metadata['id']
    url = resp.url

    # Initial metadata fetch used legacy IDs, so will only have
    # the latest minor version - if "version" is set, the
    # user requested an explicit minor (3 part version: 1.X.Y)
    # refetch metadata, using uuid and requested version
    if version and version != col_metadata['version']:
        url = '{}/contents/{}@{}'.format(base_url, uuid, version)

    # Are we baked? Did the user ask for baked? Fix it.
    if col_metadata['collated'] and not get_baked:
        url += '?as_collated=False'

    if url != resp.url:
        resp = requests.get(url)
        # Requested version, or raw (!) doesn't exist
        if resp.status_code >= 400:
            raise MissingContent(target)
        col_metadata = resp.json()

    version = col_metadata['version']
    col_id = col_metadata['legacy_id']
    col_version = col_metadata['legacy_version']

    book_id = '{}@{}'.format(uuid, version)

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
    if col_version == 'latest' or col_version is None:
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
        _write_node(tree, base_url, output_dir, book_tree,
                    get_resources, book_id, pbar)


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
                    'application/vnd.org.cnx.module': 'index.cnxml',
                    'application/vnd.org.cnx.composite-module': None,
                    }


def _safe_name(name):
    n = etree.XML('<n>{}</n>'.format(name))
    name = ''.join([t for t in n.itertext()])
    return name.replace('/', '∕').replace(':', '∶')


def gen_resources_sha1_cache(write_dir, resources):
    for resource in resources:
        with (write_dir / '.sha1sum').open('a') as s:
            # NOTE: the id is the sha1
            s.write('{}  {}\n'.format(resource['id'], resource['filename']))


def _write_node(node, base_url, out_dir, book_tree=False, get_resources=False,
                book_id=None, pbar=None, depth=None, pos={0: 0}, lvl=0):
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
    if book_id is not None:
        my_id = '{}:{}'.format(book_id, node['id'])
    else:
        my_id = node['id']
    resp = requests.get('{}/contents/{}'.format(base_url, my_id))
    if resp:  # Subcollections cannot (yet) be fetched directly
        metadata = resp.json()
        resources = {r['filename']: r for r in metadata['resources']}
        filename = filename_by_type[metadata['mediaType']]
        if not(book_tree) and filename != 'collection.xml':
            write_dir = write_dir / metadata['legacy_id']
            os.mkdir(str(write_dir))

        """Cache/store sha1-s for resources in a 'dot' file"""
        gen_resources_sha1_cache(write_dir, metadata['resources'])

        if filename:  # baked CompositeModules have no CNXML file
            url = '{}/resources/{}'.format(base_url, resources[filename]['id'])
            file_resp = requests.get(url)
            filepath = write_dir / filename
            # core files are XML - parse/serialize removes numeric entities
            # FIXME HACK hackery to extend MDML w/ uuid info, for use by
            # `push` subcommand
            file_text = file_resp.text
            try:
                xml = etree.XML(file_text)
            except ValueError:
                xml = etree.XML(file_text[file_text.find('\n') + 1:])
            ns = xml.nsmap
            ns['default'] = ns.pop(None)  # Could be cnxml or collxml
            ns['md'] = 'http://cnx.rice.edu/mdml'
            md_node = xml.xpath('//default:metadata', namespaces=ns)[0]
            if md_node.xpath('/md:document-id', namespaces=ns) == []:
                doc_uuid = (etree.
                            SubElement(md_node,
                                       '{{{md}}}document-uuid'.format(**ns)))
                doc_uuid.text = metadata['id']
            if md_node.xpath('/md:document-version', namespaces=ns) == []:
                doc_ver = (etree.
                           SubElement(md_node,
                                      '{{{md}}}document-version'.format(**ns)))
                doc_ver.text = metadata['version']
            if md_node.xpath('/md:document-hash', namespaces=ns) == []:
                doc_hash = (etree.
                            SubElement(md_node,
                                       '{{{md}}}document-hash'.format(**ns)))
                doc_hash.text = '{id}@{version}'.format(**metadata)
            filepath.write_bytes(etree.tostring(xml, encoding='utf-8',
                                                xml_declaration=True,
                                                pretty_print=True))
        if 'content' in metadata:
            filename = 'index.xhtml'
            filepath = write_dir / filename
            xml = etree.XML(metadata['content'])
            filepath.write_bytes(etree.tostring(xml, encoding='utf-8',
                                                xml_declaration=True,
                                                pretty_print=True))

        if get_resources:
            for res in resources:
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
                        book_id, pbar, depth, pos, lvl)
