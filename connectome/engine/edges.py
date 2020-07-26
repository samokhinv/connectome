from typing import Sequence, Tuple, Callable

from .base import NodeHash, Edge, NodesMask, FULL_MASK
from ..storage.disk import CacheStorage


# TODO: maybe the engine itself should deal with these
class Nothing:
    """
    A unity-like which is propagated through functional edges.
    """

    # TODO: singleton
    def __init__(self):
        raise RuntimeError("Don't init me!")

    @staticmethod
    def in_data(data):
        return any(x is Nothing for x in data)

    @staticmethod
    def in_hashes(hashes: Sequence[NodeHash]):
        return any(x.data is Nothing for x in hashes)


class FunctionEdge(Edge):
    def __init__(self, function: Callable, arity: int):
        super().__init__(arity, uses_hash=False)
        self.function = function

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash):
        if Nothing.in_data(arguments):
            return Nothing

        return self.function(*arguments)

    def _process_hashes(self, hashes: Sequence[NodeHash]) -> Tuple[NodeHash, NodesMask]:
        if Nothing.in_hashes(hashes):
            return NodeHash.from_leaf(Nothing), FULL_MASK

        return NodeHash.from_hash_nodes(
            NodeHash.from_leaf(self.function), *hashes, prev_edge=self
        ), FULL_MASK


class IdentityEdge(Edge):
    def __init__(self):
        super().__init__(arity=1, uses_hash=False)

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash):
        return arguments[0]

    def _process_hashes(self, hashes: Sequence[NodeHash]) -> Tuple[NodeHash, NodesMask]:
        return hashes[0], FULL_MASK


class CacheEdge(Edge):
    def __init__(self, storage: CacheStorage):
        super().__init__(arity=1, uses_hash=True)
        self.storage = storage

    def _process_hashes(self, hashes: Sequence[NodeHash]) -> Tuple[NodeHash, NodesMask]:
        node_hash, = hashes
        if self.storage.contains(node_hash):
            mask = []
        else:
            mask = FULL_MASK

        return node_hash, mask

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash):
        # no arguments means that the value is cached
        if not arguments:
            return self.storage.get(node_hash)

        value, = arguments
        # TODO: need a subclass for edges that interact with Nothing
        if value is Nothing:
            return value

        self.storage.set(node_hash, value)
        return value


class ProductEdge(Edge):
    def __init__(self, arity: int):
        super().__init__(arity, uses_hash=True)

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash):
        return arguments

    def _process_hashes(self, hashes: Sequence[NodeHash]) -> Tuple[NodeHash, NodesMask]:
        return NodeHash.from_hash_nodes(*hashes, prev_edge=self), FULL_MASK


# TODO: are Switch and Projection the only edges that need Nothing?
# TODO: does Nothing live only in hashes?
class SwitchEdge(Edge):
    def __init__(self, selector: Callable):
        super().__init__(arity=1, uses_hash=True)
        self.selector = selector

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash):
        if node_hash.data is Nothing:
            return Nothing

        return arguments[0]

    def _process_hashes(self, hashes: Sequence[NodeHash]) -> Tuple[NodeHash, NodesMask]:
        node_hash, = hashes
        if not self.selector(node_hash.data):
            # TODO: need a special type for hash of nothing
            node_hash = NodeHash.from_leaf(Nothing)
        return node_hash, FULL_MASK


class ProjectionEdge(Edge):
    def __init__(self):
        super().__init__(arity=1, uses_hash=True)

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash):
        # take the only non-Nothing value
        real = []
        for v in arguments[0]:
            if v is not Nothing:
                real.append(v)

        assert len(real) == 1, real
        return real[0]

    def _process_hashes(self, hashes: Sequence[NodeHash]) -> Tuple[NodeHash, NodesMask]:
        # take the only non-Nothing hash
        real = []
        for v in hashes[0].children:
            if v.data is not Nothing:
                real.append(v)

        assert len(real) == 1
        return real[0], FULL_MASK
