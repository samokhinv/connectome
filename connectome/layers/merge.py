from typing import Sequence, Any, Generator

from ..engine.edges import ConstantEdge, IdentityEdge
from ..engine.base import Node, NodeHash, Edge, NodeHashes, HashOutput, Request, Response, RequestType
from ..engine.node_hash import MergeHash
from ..utils import node_to_dict
from .base import EdgesBag


class SwitchLayer(EdgesBag):
    def __init__(self, id_to_index: dict, layers: Sequence[EdgesBag], keys_name: str):
        inputs = []
        groups = []
        edges = []
        # gather parts
        for layer in layers:
            params = layer.freeze()
            if len(params.inputs) != 1:
                raise ValueError('Each layer must have exactly one input')
            inputs.append(params.inputs[0])
            groups.append(node_to_dict(params.outputs))
            edges.extend(params.edges)

        # validate inputs
        inp = [x.name for x in inputs]
        if len(set(inp)) != 1:
            raise ValueError(f'Layer inputs must have the same name: {inp}')

        # create the new input
        inp = Node(inp[0])
        for node in inputs:
            edges.append(IdentityEdge().bind(inp, node))

        # create new outputs
        outputs = []
        common_outputs = set.intersection(*map(set, groups)) - {keys_name}
        for name in common_outputs:
            node = Node(name)
            branches = [group[name] for group in groups]
            outputs.append(node)
            edges.append(SwitchEdge(id_to_index, len(layers)).bind([inp] + branches, node))

        # and the keys
        ids = Node(keys_name)
        outputs.append(ids)
        edges.append(ConstantEdge(tuple(sorted(id_to_index))).bind([], ids))

        super().__init__([inp], outputs, edges, context=None)
        self.layers = layers


class SwitchEdge(Edge):
    def __init__(self, id_to_index: dict, n_branches: int):
        super().__init__(arity=1 + n_branches, uses_hash=True)
        self.id_to_index = id_to_index

    def compute_hash(self) -> Generator[Request, Response, HashOutput]:
        key = yield 0, RequestType.Value
        try:
            idx = self.id_to_index[key]
        except KeyError:
            raise ValueError(f'Identifier {key} not found.') from None

        value = yield idx + 1, RequestType.Hash
        return value, idx

    def evaluate(self, output: NodeHash, payload: Any) -> Generator[Request, Response, Any]:
        value = yield payload + 1, RequestType.Value
        return value

    def _hash_graph(self, inputs: NodeHashes) -> NodeHash:
        return MergeHash(*inputs)
