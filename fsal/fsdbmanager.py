import os
import re
import asyncfs
import shutil
import logging
import time
import collections
from itertools import ifilter

from .utils import fnwalk, to_unicode, to_bytes
from .fs import File, Directory
from .ondd import ONDDNotificationListener


SQL_ESCAPE_CHAR = '\\'
SQL_WILDCARDS = [('_', SQL_ESCAPE_CHAR + '_'),
                 ('%', SQL_ESCAPE_CHAR + '%')]


def sql_escape_path(path):
    for char, escaped_char in SQL_WILDCARDS:
        path = path.replace(char, escaped_char)
    return path


def checked_fnwalk(*args, **kwargs):
    walk_gen = fnwalk(*args, **kwargs)
    while True:
        try:
            yield next(walk_gen)
        except StopIteration:
            raise
        except Exception as e:
            logging.error('Error during fnwalk: %s' % str(e))


class FSDBManager(object):
    FILE_TYPE = 0
    DIR_TYPE = 1

    FS_TABLE = 'fsentries'
    STATS_TABLE = 'dbmgr_stats'

    ROOT_DIR_PATH = '.'

    PATH_LEN_LIMIT = 32767

    def __init__(self, config, context):
        base_path = os.path.abspath(config['fsal.basepath'])
        if not os.path.isdir(base_path):
            raise RuntimeError('Invalid basepath: "%s"' % (base_path))

        self.base_path = base_path
        self.db = context['databases'].fs
        blacklist = config['fsal.blacklist']
        sanitized_blacklist = []
        for p in blacklist:
            valid, p = self._validate_path(p)
            if valid:
                sanitized_blacklist.append(p)
        self.blacklist = sanitized_blacklist

        self.notification_listener = ONDDNotificationListener(config, self._handle_notification)

    def start(self):
        self.notification_listener.start()
        self._refresh_db()

    def stop(self):
        self.notification_listener.stop()

    def get_root_dir(self):
        try:
            d = Directory.from_path(self.base_path, '.')
            d.__id = 0
            return d
        except OSError:
            return None

    def list_dir(self, path):
        d = self._get_dir(path)
        if d is None:
            return (False, [])
        else:
            q = self.db.Select('*', sets=self.FS_TABLE, where='parent_id = ?')
            cursor = self.db.query(q, d.__id)
            return (True, self._fso_row_iterator(cursor))

    def search(self, query, whole_words=False, exclude=None):
        is_match, files = self.list_dir(query)
        if is_match:
            result_gen = files
        else:
            like_pattern = '%s' if whole_words else '%%%s%%'
            words = map(sql_escape_path, query.split())
            like_words = [(like_pattern % w) for w in words]
            q = self.db.Select('*', sets=self.FS_TABLE)
            for _ in like_words:
                if whole_words:
                    where_clause = 'name LIKE ?'
                else:
                    where_clause = 'lower(name) LIKE ?'
                where_clause += ' ESCAPE "%s"' % SQL_ESCAPE_CHAR
                q.where |= where_clause
            self.db.execute(q, like_words)
            result_gen = self._fso_row_iterator(self.db.cursor)

        if exclude and len(exclude) > 0:
            clean_exclude = [f.replace('.', '\.') for f in exclude]
            rxp_str = '|'.join(['^%s$' % f for f in clean_exclude])
            rxp = re.compile(rxp_str)
            result_gen = ifilter(lambda fso: rxp.match(fso.name) is None,
                                 result_gen)
        return (is_match, result_gen)

    def exists(self, path):
        return (self.get_fso(path) is not None)

    def is_dir(self, path):
        fso = self._get_dir(path)
        return (fso is not None)

    def is_file(self, path):
        fso = self._get_file(path)
        return (fso is not None)

    def get_fso(self, path):
        valid, path = self._validate_path(path)
        if not valid:
            return None
        if path == self.ROOT_DIR_PATH:
            return self.get_root_dir()
        else:
            q = self.db.Select('*', sets=self.FS_TABLE, where='path = ?')
            self.db.query(q, path)
            result = self.db.result
            return self._construct_fso(result) if result else None

    def remove(self, path):
        fso = self.get_fso(path)
        if fso is None:
            return (False, 'No such file or directory "%s"' % path)
        else:
            return self._remove_fso(fso)

    def transfer(self, src, dest):
        success, msg = self._validate_transfer(src, dest)
        if not success:
            return (success, msg)

        abs_src = os.path.abspath(src)
        abs_dest = os.path.abspath(os.path.join(self.base_path, dest))
        logging.debug('Transferring content from "%s" to "%s"' % (abs_src,
                                                                  abs_dest))
        try:
            asyncfs.move(abs_src, abs_dest)
        except (asyncfs.Error, IOError) as e:
            logging.error('Error while transfering content: %s' % str(e))
            success = False
            msg = str(e)
        self._update_db(dest)
        return (success, msg)

    def _handle_notification(self, notification):
        path = notification.path
        logging.debug("Notification received for %s" % path)
        # Find the deepest parent in hierarchy which has been indexed
        while path != '':
            if not self.exists(path):
                path = os.path.dirname(path)
            else:
                break
        if path == '':
            logging.warn("Cannot index path %s" % notification.path)
            return
        self._update_db(path)

    def _validate_path(self, path):
        if path is None or len(path.strip()) == 0:
            valid = False
        else:
            path = path.strip()
            path = path.lstrip(os.sep)
            path = path.rstrip(os.sep)
            full_path = os.path.abspath(os.path.join(self.base_path, path))
            valid = full_path.startswith(self.base_path)
            path = os.path.relpath(full_path, self.base_path)
        return (valid, path)

    def _validate_external_path(self, path):
        if path is None or len(path.strip()) == 0:
            return (False, path)
        else:
            path = path.strip()
            path = path.rstrip(os.sep)
            full_path = os.path.abspath(path)
            return (True, full_path)

    def _is_blacklisted(self, path):
        return any([path.startswith(p) for p in self.blacklist])

    def _construct_fso(self, row):
        type = row.type
        cls = Directory if type == self.DIR_TYPE else File
        fso = cls.from_db_row(self.base_path, row)
        fso.__id = row.id
        return fso

    def _remove_fso(self, fso):
        remover = shutil.rmtree if fso.is_dir() else os.remove
        try:
            remover(fso.path)
            path = sql_escape_path(fso.rel_path)
            q = self.db.Delete(self.FS_TABLE)
            q.where = 'path LIKE ? ESCAPE "%s"' % SQL_ESCAPE_CHAR
            if fso.is_dir():
                pattern = '%s' + os.sep + '%%'
                self.db.executemany(q, (((pattern % path),), (path,)))
            else:
                self.db.execute(q, (path,))

            logging.debug('Removing %d files/dirs' % (self.db.cursor.rowcount))
        except Exception as e:
            msg = 'Exception while removing "%s": %s' % (fso.rel_path, str(e))
            logging.error(msg)
            # FIXME: Handle exceptions more gracefully
            self._refresh_db()
            return (False, str(e))
        else:
            return (True, None)

    def _get_dir(self, path):
        fso = self.get_fso(path)
        return fso if fso and fso.is_dir() else None

    def _get_file(self, path):
        fso = self.get_fso(path)
        return fso if fso and fso.is_file() else None

    def _validate_transfer(self, src, dest):
        src_valid, abs_src = self._validate_external_path(src)
        dest_valid, dest = self._validate_path(dest)
        if not src_valid or not os.path.exists(abs_src) or self.exists(src):
            return (False, u'Invalid transfer source directory %s' % src)
        if not dest_valid:
            return (False, u'Invalid transfer destination directory %s' % dest)

        abs_dest = os.path.abspath(os.path.join(self.base_path, dest))
        real_dst = abs_dest
        if os.path.isdir(abs_dest):
            real_dst = os.path.join(abs_dest, asyncfs.basename(abs_src))
            if os.path.exists(real_dst):
                return (False,
                        'Destination path "%s" already exists' % real_dst)

        for path in checked_fnwalk(abs_src, lambda p: True):
            path = os.path.relpath(path, abs_src)
            dest_path = os.path.abspath(os.path.join(real_dst, path))
            if len(to_bytes(dest_path)) > self.PATH_LEN_LIMIT:
                msg = '%s exceeds path length limit' % dest_path
                return (False, msg)

        return (True, None)

    def _refresh_db(self):
        start = time.time()
        self._prune_db()
        self._update_db()
        end = time.time()
        logging.debug('DB refreshed in %0.3f ms' % ((end - start) * 1000))

    def _prune_db(self, batch_size=1000):
        with self.db.transaction():
            q = self.db.Select('path', sets=self.FS_TABLE)
            self.db.query(q)
            cursor = self.db.drop_cursor()
            removed_paths = []
            for result in cursor:
                path = result.path
                full_path = os.path.join(self.base_path, path)
                if not os.path.exists(full_path) or self._is_blacklisted(path):
                    logging.debug('Removing db entry for "%s"' % path)
                    removed_paths.append(path)
                if len(removed_paths) >= batch_size:
                    self._remove_paths(removed_paths)
                    removed_paths = []
            if len(removed_paths) >= 0:
                self._remove_paths(removed_paths)

    def _remove_paths(self, paths):
        q = self.db.Delete(self.FS_TABLE, where='path = ?')
        self.db.executemany(q, ((p,) for p in paths))

    def _update_db(self, src_path=ROOT_DIR_PATH):
        def checker(path):
            result = (path != self.base_path and not os.path.islink(path))
            rel_path = os.path.relpath(path, self.base_path)
            result = result and not self._is_blacklisted(rel_path)
            return result

        src_path = os.path.abspath(os.path.join(self.base_path, src_path))
        src_path = to_unicode(src_path)
        if not os.path.exists(src_path):
            logging.error('Cannot index "%s". Path does not exist' % src_path)
            return
        id_cache = FIFOCache(1024)
        with self.db.transaction():
            for path in checked_fnwalk(src_path, checker):
                rel_path = os.path.relpath(path, self.base_path)
                parent_path, name = os.path.split(rel_path)
                parent_id = id_cache[parent_path] if parent_path in id_cache else None
                if os.path.isdir(path):
                    fso = Directory.from_path(self.base_path, rel_path)
                else:
                    fso = File.from_path(self.base_path, rel_path)
                old_fso = self.get_fso(rel_path)
                if not old_fso or old_fso != fso:
                    fso_id = self._update_fso_entry(fso, parent_id)
                    logging.debug('Updating db entry for "%s"' % rel_path)
                    if fso.is_dir():
                        id_cache[fso.rel_path] = fso_id

    def _update_fso_entry(self, fso, parent_id=None):
        if not parent_id:
            parent, name = os.path.split(fso.rel_path)
            parent_dir = self._get_dir(parent)
            parent_id = parent_dir.__id if parent_dir else 0

        cols = ['parent_id', 'type', 'name', 'size', 'create_time',
                'modify_time', 'path']
        q = self.db.Replace(self.FS_TABLE, cols=cols)
        size = fso.size if hasattr(fso, 'size') else 0
        type = self.DIR_TYPE if fso.is_dir() else self.FILE_TYPE
        values = [parent_id, type, fso.name, size, fso.create_date,
                  fso.modify_date, fso.rel_path]
        entry = self.get_fso(fso.rel_path)
        if entry:
            cols.append('id')
            values.append(entry.__id)
        self.db.execute(q, values)
        q = self.db.Select('last_insert_rowid() as id')
        self.db.execute(q)
        return self.db.result.id

    def _clear_db(self):
        with self.db.transaction():
            q = self.db.Delete(self.FS_TABLE)
            self.db.execute(q)

    def _fso_row_iterator(self, cursor):
        for result in cursor:
            yield self._construct_fso(result)


class FIFOCache(object):

    def __init__(self, maxsize):
        self.maxsize = maxsize
        self.cache = collections.OrderedDict()

    def __contains__(self, key):
        return (key in self.cache)

    def __getitem__(self, key):
        try:
            return self.cache[key]
        except KeyError:
            return None

    def __setitem__(self, key, value):
        if len(self.cache) >= self.maxsize:
            self.cache.popitem(False)
        self.cache[key] = value
