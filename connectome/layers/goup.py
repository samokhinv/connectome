from collections import defaultdict
from hashlib import sha256
from typing import Sequence, Any

from .base import EdgesBag, Wrapper, NoContext
from ..engine import NodeHash
from ..engine.base import Node, TreeNode, NodeHashes, NodesMask, FULL_MASK, Edge
from ..engine.edges import FunctionEdge, ProductEdge
from ..engine.graph import Graph
from ..engine.node_hash import HashType


class GroupLayer(Wrapper):
    def __init__(self, name: str):
        self.name = name

    @staticmethod
    def _find(nodes, name):
        for node in nodes:
            if node.name == name:
                return node

        raise ValueError(f'The previous layer must contain the attribute "{name}"')

    def wrap(self, layer: EdgesBag) -> EdgesBag:
        main = layer.freeze()

        inp, = main.inputs
        edges = list(main.edges)
        outputs = []
        mapping = TreeNode.from_edges(edges)
        changed_input = Node('id')
        mapping_node = Node('$mapping')
        ids_node = self._find(main.outputs, 'ids')
        outputs.append(changed_input)

        # create a mapping: {new_id: [old_ids]}
        edges.append(MappingEdge(Graph([mapping[inp]], mapping[self._find(main.outputs, self.name)])).bind(
            [ids_node], mapping_node))

        # evaluate each output
        for node in main.outputs:
            if node.name in [self.name, 'ids', 'id']:
                continue

            output = Node(node.name)
            outputs.append(output)
            edges.append(GroupEdge(Graph([mapping[inp]], mapping[node])).bind(
                [changed_input, mapping_node], output))

        # update ids
        output_ids = Node('ids')
        outputs.append(output_ids)
        edges.append(FunctionEdge(extract_keys, arity=1).bind(mapping_node, output_ids))

        return EdgesBag([changed_input], outputs, edges, NoContext())


class MappingEdge(Edge):
    def __init__(self, graph):
        super().__init__(arity=1, uses_hash=True)
        self.graph = graph
        self._mapping = None

    def _propagate_hash(self, inputs: NodeHashes) -> NodeHash:
        return NodeHash.from_hash_nodes(
            *inputs, self.graph.hash(),
            kind=HashType.MAPPING,
        )

    def _compute_mask(self, inputs: NodeHashes, output: NodeHash) -> NodesMask:
        if self._mapping is not None:
            return []
        return FULL_MASK

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash) -> Any:
        if self._mapping is not None:
            return self._mapping

        mapping = defaultdict(list)
        for i in arguments[0]:
            mapping[self.graph.eval(i)].append(i)

        self._mapping = mapping = {k: tuple(sorted(v)) for k, v in mapping.items()}
        return mapping

    def _hash_graph(self, inputs: NodeHashes) -> NodeHash:
        return self._propagate_hash(inputs)


class GroupEdge(Edge):
    def __init__(self, graph):
        super().__init__(arity=2, uses_hash=True)
        self.graph = graph

    def _propagate_hash(self, inputs: NodeHashes) -> NodeHash:
        return NodeHash.from_hash_nodes(
            *inputs, self.graph.hash(),
            kind=HashType.GROUPING,
        )

    def _compute_mask(self, inputs: NodeHashes, output: NodeHash) -> NodesMask:
        return FULL_MASK

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash) -> Any:
        # get the required ids
        ids = arguments[1][arguments[0]]

        result = {}
        for i in ids:
            result[i] = self.graph.eval(i)

        return result

    def _hash_graph(self, inputs: NodeHashes) -> NodeHash:
        return self._propagate_hash(inputs)


def extract_keys(d):
    return tuple(sorted(d))


# prototype for multiple groupby


class MultiGroupLayer(Wrapper):
    def __init__(self, comparators: dict):
        self.names = sorted(comparators)
        self.comparators = [comparators[x] for x in self.names]

    @staticmethod
    def _find(nodes, name):
        for node in nodes:
            if node.name == name:
                return node

        raise ValueError(f'The previous layer must contain the attribute "{name}"')

    def wrap(self, layer: EdgesBag) -> EdgesBag:
        main = layer.freeze()

        inp, = main.inputs
        edges = list(main.edges)
        graph_outputs, group_outputs = [], []

        for node in main.outputs:
            if node.name not in {'id', 'ids'}:
                if node.name in self.names:
                    graph_outputs.append(node)
                else:
                    group_outputs.append(node)
        assert len(graph_outputs) == len(self.names)

        # create a mapping: {new_id: [old_ids]}
        graph_output = Node('$product')
        mapping_node = Node('$mapping')
        ids_node = self._find(main.outputs, 'ids')
        edges.append(ProductEdge(len(graph_outputs)).bind(graph_outputs, graph_output))
        mapping = TreeNode.from_edges(edges)

        edges.append(HashMappingEdge(Graph([mapping[inp]], mapping[graph_output]), self.comparators).bind(
            [ids_node], mapping_node))

        # evaluate each output
        changed_input = Node('id')
        outputs = [changed_input]
        for node in group_outputs:
            output = Node(node.name)
            outputs.append(output)
            edges.append(GroupEdge(Graph([mapping[inp]], mapping[node])).bind(
                [changed_input, mapping_node], output))

        # update ids
        output_ids = Node('ids')
        outputs.append(output_ids)
        edges.append(FunctionEdge(extract_keys, arity=1).bind(mapping_node, output_ids))

        return EdgesBag([changed_input], outputs, edges, NoContext())


class HashMappingEdge(Edge):
    def __init__(self, graph, comparators):
        super().__init__(arity=1, uses_hash=True)
        self.graph = graph
        self._mapping = None
        self.comparators = comparators
        self.hasher = sha256

    def _propagate_hash(self, inputs: NodeHashes) -> NodeHash:
        return NodeHash.from_hash_nodes(
            *inputs, *(NodeHash.from_leaf(x) for x in self.comparators), NodeHash.from_leaf(self.hasher),
            self.graph.hash(),
            kind=HashType.MULTI_MAPPING,
        )

    def _compute_mask(self, inputs: NodeHashes, output: NodeHash) -> NodesMask:
        if self._mapping is not None:
            return []
        return FULL_MASK

    def _evaluate(self, arguments: Sequence, mask: NodesMask, node_hash: NodeHash) -> Any:
        if self._mapping is not None:
            return self._mapping

        groups = []
        for i in arguments[0]:
            keys = self.graph.eval(i)
            assert len(keys) == len(self.comparators)
            # either find a group
            for entry, container in groups:
                if all(cmp(x, y) for cmp, x, y in zip(self.comparators, entry, keys)):
                    container.append(i)
                    break
            # or create a new one
            else:
                groups.append((keys, [i]))

        mapping = {}
        for _, ids in groups:
            ids = tuple(sorted(ids))
            # double hashing lets us get rid of separators
            hashes = b''.join(self.hasher(i.encode()).digest() for i in ids)
            mapping[self.hasher(hashes).hexdigest()] = ids

        assert len(mapping) == len(groups)
        self._mapping = mapping
        return mapping

    def _hash_graph(self, inputs: NodeHashes) -> NodeHash:
        return self._propagate_hash(inputs)
