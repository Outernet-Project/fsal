# -*- coding: utf-8 -*-

"""
responses.py: response builders for FSAL

Copyright 2014-2015, Outernet Inc.
Some rights reserved.

This software is free software licensed under the terms of GPLv3. See COPYING
file that comes with the source code, or http://www.gnu.org/licenses/gpl.txt.
"""

from datetime import datetime

from xml.etree.ElementTree import Element, SubElement, tostring

from . import commandtypes
from .utils import to_unicode
from .serialize import singular_name


def create_response_xml_root():
    return Element(u'response')


def to_timestamp(dt, epoch=datetime(1970, 1, 1)):
    delta = dt - epoch
    return delta.total_seconds()


def dict_to_xml(data, root=None):
    root = Element(u'response') if root is None else root
    for key, value in data.items():
        subnode = SubElement(root, key)
        if isinstance(value, dict):
            dict_to_xml(value, root=subnode)
        elif isinstance(value, list):
            for v in value:
                dict_to_xml({singular_name(key): v}, root=subnode)
        else:
            subnode.text = to_unicode(value)
    return root


class GenericResponse:

    def __init__(self, response_data):
        self.response_data = response_data

    def get_xml(self):
        return dict_to_xml(self.response_data)

    def get_xml_str(self, encoding='utf-8'):
        return tostring(self.get_xml(), encoding=encoding)


def add_fso_node(parent_node, fso):
    node_name = u'dir' if fso.is_dir() else u'file'
    fso_node = SubElement(parent_node, node_name)
    base_path_node = SubElement(fso_node, u'base-path')
    base_path_node.text = to_unicode(fso.base_path)
    rel_path_node = SubElement(fso_node, u'rel-path')
    rel_path_node.text = to_unicode(fso.rel_path)
    create_timestamp_node = SubElement(fso_node, u'create-timestamp')
    create_timestamp_node.text = to_unicode(to_timestamp(fso.create_date))
    modify_timestamp_node = SubElement(fso_node, u'modify-timestamp')
    modify_timestamp_node.text = to_unicode(to_timestamp(fso.modify_date))
    size_node = SubElement(fso_node, u'size')
    size_node.text = str(fso.size)


def add_event_node(parent_node, event):
    event_node = SubElement(parent_node, u'event')
    type_node = SubElement(event_node, u'type')
    type_node.text = to_unicode(event.event_type)
    src_node = SubElement(event_node, u'src')
    src_node.text = to_unicode(event.src)
    is_dir_node = SubElement(event_node, u'is_dir')
    is_dir_node.text = to_unicode(event.is_dir).lower()


class ConsolidateResponse(GenericResponse):

    def get_xml(self):
        root = create_response_xml_root()
        result_node = SubElement(root, u'result')
        success_node = SubElement(result_node, u'success')
        success = self.response_data['success']
        success_node.text = to_unicode(success).lower()
        is_partial_node = SubElement(result_node, u'is_partial')
        is_partial = self.response_data['params']['is_partial']
        is_partial_node.text = to_unicode(is_partial).lower()
        msg_node = SubElement(result_node, u'message')
        msg_node.text = self.response_data['params']['message']
        return root


class ListBasePathsResponse(GenericResponse):

    def get_xml(self):
        root = create_response_xml_root()
        result_node = SubElement(root, u'result')
        success_node = SubElement(result_node, u'success')
        success = self.response_data['success']
        success_node.text = to_unicode(success).lower()
        if success:
            return self.response_data['base_paths']
        return root


class DirectoryListingResponse(GenericResponse):

    def get_xml(self):
        root = create_response_xml_root()
        result_node = SubElement(root, u'result')
        success_node = SubElement(result_node, u'success')
        success = self.response_data['success']
        success_node.text = to_unicode(success).lower()
        if success:
            params_node = SubElement(result_node, u'params')
            count = self.response_data['params'].get('count')
            if count:
                count_node = SubElement(result_node, u'count')
                count_node.text = to_unicode(count)

            dirs_node = SubElement(params_node, u'dirs')
            for d in self.response_data['params'].get('dirs', []):
                add_fso_node(dirs_node, d)

            files_node = SubElement(params_node, u'files')
            for f in self.response_data['params'].get('files', []):
                add_fso_node(files_node, f)

        return root


class SearchResponse(GenericResponse):

    def get_xml(self):
        root = create_response_xml_root()
        result_node = SubElement(root, u'result')
        success_node = SubElement(result_node, u'success')
        success = self.response_data['success']
        success_node.text = to_unicode(success).lower()
        if success:
            params_node = SubElement(result_node, u'params')

            is_match = to_unicode(self.response_data['params']['is_match'])
            is_match_node = SubElement(params_node, u'is-match')
            is_match_node.text = is_match.lower()

            dirs_node = SubElement(params_node, u'dirs')
            for d in self.response_data['params']['dirs']:
                add_fso_node(dirs_node, d)

            files_node = SubElement(params_node, u'files')
            for f in self.response_data['params']['files']:
                add_fso_node(files_node, f)

        return root


class GetFSOResponse(GenericResponse):

    def get_xml(self):
        root = create_response_xml_root()
        result_node = SubElement(root, u'result')
        success_node = SubElement(result_node, u'success')
        success = self.response_data['success']
        success_node.text = to_unicode(success).lower()
        if success:
            params = self.response_data['params']
            params_node = SubElement(result_node, u'params')
            fso = params['dir'] if 'dir' in params else params['file']
            add_fso_node(params_node, fso)
        else:
            error_node = SubElement(result_node, u'error')
            error_node.text = to_unicode(self.response_data['params']['error'])

        return root


class GetChangesResponse(GenericResponse):

    def get_xml(self):
        root = create_response_xml_root()
        result_node = SubElement(root, u'result')
        events_node = SubElement(result_node, u'events')
        for e in self.response_data['params']['events']:
            add_event_node(events_node, e)
        return root


class CommandResponseFactory:
    default_response_generator = GenericResponse
    response_map = {
        commandtypes.COMMAND_TYPE_CONSOLIDATE: ConsolidateResponse,
        commandtypes.COMMAND_TYPE_LIST_DIR: DirectoryListingResponse,
        commandtypes.COMMAND_TYPE_LIST_DESCENDANTS: DirectoryListingResponse,
        commandtypes.COMMAND_TYPE_FILTER: DirectoryListingResponse,
        commandtypes.COMMAND_TYPE_SEARCH: SearchResponse,
        commandtypes.COMMAND_TYPE_GET_FSO: GetFSOResponse,
        commandtypes.COMMAND_TYPE_GET_CHANGES: GetChangesResponse,
    }

    def create_response(self, response_data):
        command_type = response_data['type']
        try:
            response_generator = self.response_map[command_type]
        except KeyError:
            response_generator = self.default_response_generator

        return response_generator(response_data)
