import os
import logging

import zippie

from .utils import common_ancestor


def verify_paths(paths, extract_path):
    for path in paths:
        abspath = os.path.abspath(os.path.join(extract_path, path))
        if not abspath.startswith(extract_path):
            return False
    else:
        return True

def extract_zip_bundle(bundle_path, extract_path):
    success = False
    files = []
    try:
        zfile = zippie.PieZipFile(bundle_path)
        files = zfile.namelist()
        if not verify_paths(files, extract_path):
            raise RuntimeError('Invalid paths used in bundle: {}'.format(str(files)))
        # TODO: Add check for testing integrity of zip bundle
        zfile.extractall(extract_path)
        success = True
    except (RuntimeError, zippie.BadZipFile) as e:
        logging.exception('Error while extracting zip bundle: {}'.format(str(e)))
    return success, files

def abs_bundle_path(base_path, bundle_path):
    return os.path.abspath(os.path.join(base_path, bundle_path))


class BundleExtracter(object):
    def __init__(self, config):
        self.bundles_dir = config['bundles.bundles_dir']
        self.bundles_exts = config['bundles.bundles_exts']

    def is_bundle(self, base_path, path):
        abspath = abs_bundle_path(base_path, path)
        if os.path.isfile(abspath):
            ext = os.path.splitext(path)[1][1:]
            return common_ancestor(path, self.bundles_dir) != '' and ext in self.bundles_exts
        return False

    def extract_bundle(self, bundle_path, base_path):
        if not self.is_bundle(base_path, bundle_path):
           raise RuntimeError('{} is not a recognized bundle.'.format(bundle_path))
        extracter = self._get_extracter(bundle_path)
        abspath = abs_bundle_path(base_path, bundle_path)
        success, paths = extracter(abspath, base_path)
        return success, paths

    def _get_extracter(self, bundle_path):
        #TODO: Detect the extracter to be used based on the path
        return extract_zip_bundle

