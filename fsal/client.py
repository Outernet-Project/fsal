
import contextlib
import functools
import socket

import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element, SubElement, tostring

from . import commandtypes
from .fs import File, Directory
from .events import event_from_xml
from .utils import to_unicode
from .serialize import str_to_bool, bool_to_str, singular_name
from .exceptions import OpenError


IN_ENCODING = 'utf-8'
OUT_ENCODING = 'utf-8'


def build_request_xml(command, params):
    root = Element('request')
    command_node = SubElement(root, 'command')
    type_node = SubElement(command_node, 'type')
    type_node.text = command
    params_node = SubElement(command_node, 'params')
    for key, value in params.iteritems():
        param_node = SubElement(params_node, key)
        if isinstance(value, list):
            add_list_xml(value, param_node)
        elif hasattr(value, '__iter__'):
            # Convert iterables to list
            add_list_xml(list(value), param_node)
        else:
            param_node.text = to_unicode(value)
    return root


def add_list_xml(items, parent):
    """
    Add each element of a flat list as child node to parent
    """
    for item in items:
        node = SubElement(parent, singular_name(parent.tag))
        node.text = item


def read_socket_stream(sock, buff_size=2048):
    data = buff = sock.recv(buff_size)
    while buff and '\0' not in buff:
        buff = sock.recv(buff_size)
        data += buff
    return data[:-1]


def command(command_type, response_parser):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            params = func(self, *args, **kwargs)
            request_xml = build_request_xml(command_type, params)
            response = self._send_request(tostring(request_xml))
            response_xml = ET.fromstring(response)
            return response_parser(self, response_xml)
        return wrapper
    return decorator


def iter_fsobjs(xml_node, constructor_func):
    for child in xml_node:
        yield constructor_func(child)


def sort_listing(fso_list):
    """
    Sort list of FSObject in-place
    """
    fso_list.sort(key=lambda fso: fso.name)


class FSAL(object):

    def __init__(self, socket_path):
        self.socket_path = socket_path

    def _send_request(self, message):
        if not message[-1] == '\0':
            message = message.encode(OUT_ENCODING) + '\0'
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
            sock.sendall(message)
            return read_socket_stream(sock)
        except socket.error:
            if sock:
                sock.close()
            sock = None
            raise RuntimeError('FSAL could not connect to FSAL server')

    def _parse_list_dir_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        dirs = []
        files = []
        if success:
            dirs_node = response_xml.find('.//dirs')
            files_node = response_xml.find('.//files')
            dirs = list(iter_fsobjs(dirs_node, Directory.from_xml))
            files = list(iter_fsobjs(files_node, File.from_xml))
            sort_listing(dirs)
            sort_listing(files)
        return (success, dirs, files)

    def _parse_list_descendants_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        dirs = []
        files = []
        count = 0
        if success:
            count_node = response_xml.find('.//count')
            count = int(count_node.text) if count_node is not None else None
            dirs_node = response_xml.find('.//dirs')
            files_node = response_xml.find('.//files')
            dirs = list(iter_fsobjs(dirs_node, Directory.from_xml))
            files = list(iter_fsobjs(files_node, File.from_xml))
        return (success, count, dirs, files)

    def _parse_exists_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        exists_node = response_xml.find('.//exists')
        exists = str_to_bool(exists_node.text)
        return success and exists

    def _parse_isdir_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        isdir_node = response_xml.find('.//isdir')
        isdir = str_to_bool(isdir_node.text)
        return success and isdir

    def _parse_isfile_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        isfile_node = response_xml.find('.//isfile')
        isfile = str_to_bool(isfile_node.text)
        return success and isfile

    def _parse_remove_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        error_node = response_xml.find('.//error')
        error = error_node.text
        return (success, error)

    def _parse_search_response(self, response_xml):
        success, dirs, files = self._parse_list_dir_response(response_xml)
        is_match = (success and
                    str_to_bool(response_xml.find('.//is-match').text))
        return (dirs, files, is_match)

    def _parse_get_fso_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        if success:
            dir_node = response_xml.find('.//dir')
            if dir_node is not None:
                return (success, Directory.from_xml(dir_node))

            file_node = response_xml.find('.//file')
            return (success, File.from_xml(file_node))
        else:
            error_node = response_xml.find('.//error')
            error = error_node.text
            return (success, error)

    def _parse_transfer_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        error_node = response_xml.find('.//error')
        error = error_node.text
        return (success, error)

    def _parse_get_changes_response(self, response_xml):
        events = []
        events_node = response_xml.find('.//events')
        for child in events_node:
            events.append(event_from_xml(child))
        return events

    def _parse_confirm_changes_response(self, response_xml):
        return None

    def _parse_empty_response(self, response_xml):
        return None

    def _parse_generic_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        error_node = response_xml.find('.//error')
        error = error_node.text
        return (success, error)

    def _parse_base_paths_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        params_node = response_xml.find('.//params')
        path_nodes = params_node.findall('.//path')
        paths = [p.text for p in path_nodes]
        return success, paths

    def _parse_get_path_size_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        size = int(response_xml.find('.//size').text)
        return success, size

    @command(commandtypes.COMMAND_TYPE_GET_PATH_SIZE, _parse_get_path_size_response)
    def get_path_size(self, path):
        """ Moves content from a list of sources to a single destination """
        return {'path': path}

    def _parse_consolidate_response(self, response_xml):
        success_node = response_xml.find('.//success')
        success = str_to_bool(success_node.text)
        is_partial_node = response_xml.find('.//is_partial')
        is_partial = str_to_bool(is_partial_node.text)
        message_node = response_xml.find('.//message')
        message = message_node.text
        return success, is_partial, message

    @command(commandtypes.COMMAND_TYPE_CONSOLIDATE, _parse_consolidate_response)
    def consolidate(self, sources, destination):
        """ Moves content from a list of sources to a single destination """
        return {'sources': sources, 'dest': destination}

    @command(commandtypes.COMMAND_TYPE_LIST_BASE_PATHS, _parse_base_paths_response)
    def list_base_paths(self):
        """ Returns a list of all registered base paths in FSAL """
        return {}

    @command(commandtypes.COMMAND_TYPE_LIST_DIR, _parse_list_dir_response)
    def list_dir(self, path):
        return {'path': path}

    @command(commandtypes.COMMAND_TYPE_LIST_DESCENDANTS, _parse_list_descendants_response)
    def list_descendants(self, path, count=False, offset=None, limit=None,
                         order=None, span=None, entry_type=None, ignored_paths=None):
        params = {'path': path, 'count': bool_to_str(count)}
        if order is not None:
            params['order'] = order
        if offset is not None:
            params['offset'] = offset
        if limit is not None:
            params['limit'] = limit
        if span is not None:
            params['span'] = span
        if entry_type is not None:
            params['entry_type'] = entry_type
        if ignored_paths is not None:
            params['ignored_paths'] = ignored_paths
        return params

    @command(commandtypes.COMMAND_TYPE_EXISTS, _parse_exists_response)
    def exists(self, path, unindexed=False):
        return {
            'path': path,
            'unindexed': bool_to_str(unindexed)
        }

    @command(commandtypes.COMMAND_TYPE_ISDIR, _parse_isdir_response)
    def isdir(self, path):
        return {'path': path}

    @command(commandtypes.COMMAND_TYPE_ISFILE, _parse_isfile_response)
    def isfile(self, path):
        return {'path': path}

    @command(commandtypes.COMMAND_TYPE_REMOVE, _parse_remove_response)
    def remove(self, path):
        return {'path': path}

    @command(commandtypes.COMMAND_TYPE_SEARCH, _parse_search_response)
    def search(self, query, whole_words=False, exclude=None):
        return {'query': query,
                'whole_words': bool_to_str(whole_words),
                'excludes': exclude}

    @command(commandtypes.COMMAND_TYPE_FILTER, _parse_list_dir_response)
    def filter(self, paths):
        """
        Return a subset of all file system objects from the database which
        paths can be found in the passed in list.
        """
        return {'paths': paths}

    @command(commandtypes.COMMAND_TYPE_GET_FSO, _parse_get_fso_response)
    def get_fso(self, path):
        return {'path': path}

    @command(commandtypes.COMMAND_TYPE_TRANSFER, _parse_transfer_response)
    def transfer(self, src, dest):
        return {'src': src, 'dest': dest}

    def get_changes(self, limit=100):
        for e in self._get_changes_helper(limit):
            yield e
        self.confirm_changes(limit)

    @command(commandtypes.COMMAND_TYPE_GET_CHANGES, _parse_get_changes_response)
    def _get_changes_helper(self, limit=100):
        return {'limit': limit}

    @command(commandtypes.COMMAND_TYPE_CONFIRM_CHANGES,
             _parse_confirm_changes_response)
    def confirm_changes(self, limit):
        return {'limit': limit}

    @contextlib.contextmanager
    def open(self, path, mode):
        (success, fso) = self.get_fso(path)
        if not success:
            raise OpenError(fso)

        file_obj = open(fso.path, mode)
        try:
            yield file_obj
        except Exception:
            file_obj.close()
            raise
        finally:
            self.refresh_path(fso.rel_path)

    @command(commandtypes.COMMAND_TYPE_REFRESH_PATH, _parse_generic_response)
    def refresh_path(self, path):
        return {'path': path}

    @command(commandtypes.COMMAND_TYPE_REFRESH, _parse_empty_response)
    def refresh(self):
        return {}

    @command(commandtypes.COMMAND_TYPE_SET_WHITELIST, _parse_empty_response)
    def set_whitelist(self, paths):
        return {'paths': paths}
