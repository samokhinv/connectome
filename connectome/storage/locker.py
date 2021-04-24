import logging
import time
from abc import ABC, abstractmethod
from threading import Lock
from typing import ContextManager, MutableMapping

from redis import Redis
from sqlitedict import SqliteDict

from ..utils import PathLike

Key = str
logger = logging.getLogger(__name__)


class Locker(ABC):
    def __init__(self, track_size: bool):
        self.track_size = track_size

    @abstractmethod
    def start_reading(self, key: Key) -> bool:
        """ Try to reserve a read operation. Return True if it was successful. """

    @abstractmethod
    def stop_reading(self, key: Key):
        """ Release a read operation. """
        pass

    @abstractmethod
    def start_writing(self, key: Key) -> bool:
        """ Try to reserve a write operation. Return True if it was successful. """

    @abstractmethod
    def stop_writing(self, key: Key):
        """ Release a write operation. """

    def get_size(self):
        raise NotImplementedError

    def inc_size(self, size: int):
        raise NotImplementedError

    def set_size(self, size: int):
        raise NotImplementedError


class DummyLocker(Locker):
    def __init__(self):
        super().__init__(False)

    def start_reading(self, key: Key) -> bool:
        return True

    def stop_reading(self, key: Key):
        pass

    def start_writing(self, key: Key) -> bool:
        return True

    def stop_writing(self, key: Key):
        pass


class DictRegistry:
    _lock: ContextManager
    _reading: MutableMapping[Key, int]
    _writing: MutableMapping[Key, int]

    def _get_reading(self, key):
        value = self._reading.get(key, 0)
        logger.info(f'Read count {value}')
        assert value >= 0, value
        return value

    def _get_writing(self, key):
        value = self._writing.get(key, 0)
        logger.info(f'Write count {value}')
        assert 0 <= value <= 1, value
        return value

    def _is_reading(self, key: Key):
        return bool(self._get_reading(key))

    def _is_writing(self, key: Key):
        return bool(self._get_writing(key))

    def start_reading(self, key: Key) -> bool:
        with self._lock:
            if self._is_writing(key):
                return False

            self._reading[key] = self._get_reading(key) + 1
            return True

    def stop_reading(self, key: Key):
        with self._lock:
            value = self._get_reading(key)
            if value == 1:
                self._reading.pop(key)
            else:
                self._reading[key] = value - 1

    def start_writing(self, key: Key) -> bool:
        with self._lock:
            if self._is_reading(key) or self._is_writing(key):
                return False

            value = self._get_writing(key)
            assert value == 0, value
            self._writing[key] = value + 1
            return True

    def stop_writing(self, key: Key):
        with self._lock:
            value = self._get_writing(key)
            assert value == 1, value
            self._writing.pop(key)


class ThreadLocker(DictRegistry, Locker):
    def __init__(self):
        super().__init__(False)
        self._lock = Lock()
        self._reading = {}
        self._writing = {}


class RedisLocker(Locker):
    def __init__(self, redis: Redis, prefix: str):
        super().__init__(True)
        self._redis = redis
        self._prefix = prefix
        self._volume_key = f'{self._prefix}.V'
        self._lock_key = f'{self._prefix}.L'
        # TODO: how slow are these checks?
        # language=Lua
        self._stop_writing = self._redis.script_load('''
        if redis.call('hget', KEYS[1], ARGV[1]) == '-1' then 
            redis.call('hdel', KEYS[1], ARGV[1]) else error('') 
        end''')
        # language=Lua
        self._start_reading = self._redis.script_load('''
        if redis.call('hget', KEYS[1], ARGV[1]) == '-1' then 
            return 0 else redis.call('hincrby', KEYS[1], ARGV[1], 1); return 1 
        end''')
        # language=Lua
        self._stop_reading = self._redis.script_load('''
        local lock = redis.call('hget', KEYS[1], ARGV[1])
        if lock == '1' then
            redis.call('hdel', KEYS[1], ARGV[1]) 
        elseif tonumber(lock) < 1 then
            error('')
        else
            redis.call('hincrby', KEYS[1], ARGV[1], -1)
        end''')

    def start_writing(self, key: Key) -> bool:
        return bool(self._redis.hsetnx(self._lock_key, key, -1))

    def stop_writing(self, key: Key):
        self._redis.evalsha(self._stop_writing, 1, self._lock_key, key)

    def start_reading(self, key: Key) -> bool:
        return bool(self._redis.evalsha(self._start_reading, 1, self._lock_key, key))

    def stop_reading(self, key: Key):
        self._redis.evalsha(self._stop_reading, 1, self._lock_key, key)

    def get_size(self):
        return int(self._redis.get(self._volume_key))

    def set_size(self, size: int):
        self._redis.set(self._volume_key, size)

    def inc_size(self, size: int):
        self._redis.incrby(self._volume_key, size)

    @classmethod
    def from_url(cls, url: str, prefix: str):
        return cls(Redis.from_url(url), prefix)


class SqliteLocker(DictRegistry, Locker):
    def __init__(self, path: PathLike):
        def identity(x):
            return x

        super().__init__(True)
        self._lock = SqliteDict(path, 'lock')
        self._reading = SqliteDict(
            path, autocommit=True, tablename='reading', encode=identity, decode=identity
        )
        self._writing = SqliteDict(
            path, autocommit=True, tablename='writing', encode=identity, decode=identity
        )
        self._meta = SqliteDict(
            path, autocommit=True, tablename='meta', encode=identity, decode=identity
        )

    def get_size(self):
        return self._meta.get('volume', 0)

    def set_size(self, size: int):
        self._meta['volume'] = size

    def inc_size(self, size: int):
        self.set_size(self.get_size() + size)


def wait_for_true(func, key, sleep_time, max_iterations):
    i = 0
    while not func(key):
        if i >= max_iterations:
            logger.error('Potential deadlock detected for %s', key)
            raise RuntimeError(f"It seems like you've hit a deadlock for key {key}.")

        time.sleep(sleep_time)
        i += 1

    logger.debug('Waited for %d iterations for %s', i, key)
