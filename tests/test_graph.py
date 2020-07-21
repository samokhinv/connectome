import pytest

from connectome.layers import PipelineLayer, MemoryCacheLayer


def test_single(first_simple):
    assert first_simple.get_forward_method('sum')(1, 2) == 3
    assert first_simple.get_forward_method('sub')(1, 2) == -1
    assert first_simple.get_forward_method('squared')(9) == 81


def test_duplicates(layer_builder):
    double = layer_builder.build_layer(x=lambda x: 2 * x)
    assert double.get_forward_method('x')(4) == 8
    eight = PipelineLayer(
        double, double, double,
    )
    assert eight.get_forward_method('x')(4) == 32


def test_chain(first_simple, second_simple, third_simple):
    chain = PipelineLayer(first_simple)

    assert chain.get_forward_method('sum')(1, 2) == 3
    assert chain.get_forward_method('squared')(4) == 16

    chain = PipelineLayer(first_simple, second_simple)
    assert chain.get_forward_method('prod')(7) == 7 ** 5
    assert chain.get_forward_method('min')(3) == 9
    assert chain.get_forward_method('sub')(5, 3) == 2

    chain = PipelineLayer(first_simple, second_simple, third_simple)
    assert chain.get_forward_method('div')(7) == 7 ** 4
    assert chain.get_forward_method('original')(x=9, y=10) == 9


def test_cache(layer_builder):
    def counter(x):
        nonlocal count
        count += 1
        return x

    count = 0
    first = layer_builder.build_layer(x=counter)
    assert first.get_forward_method('x')(1) == 1
    assert count == 1

    chain = PipelineLayer(first, MemoryCacheLayer(names=['x']))
    assert chain.get_forward_method('x')(1) == 1
    assert count == 2
    assert chain.get_forward_method('x')(1) == 1
    assert count == 2

    assert chain.get_forward_method('x')(2) == 2
    assert count == 3
    assert chain.get_forward_method('x')(2) == 2
    assert count == 3


def test_slicing(first_simple, second_simple, third_simple):
    chain = PipelineLayer(first_simple, second_simple, third_simple)

    assert chain.slice(1, 3).get_forward_method('div')(squared=4, cube=3, x=3) == 4
    assert chain.slice(0, 1).get_forward_method('sum')(x=2, y=10) == 12
    assert chain.slice(0, 2).get_forward_method('min')(x=5) == 25


def test_backward_methods(first_backward, second_backward):
    assert first_backward.get_backward_method('prod')(10) == 5
    assert first_backward.get_backward_method('prod')(first_backward.get_forward_method('prod')(15)) == 15

    first_backward = PipelineLayer(first_backward)
    assert first_backward.get_backward_method('prod')(10) == 5
    assert first_backward.get_backward_method('prod')(first_backward.get_forward_method('prod')(15)) == 15

    chain = PipelineLayer(first_backward, second_backward)
    assert chain.get_forward_method('prod')(10) == '21'
    assert chain.get_backward_method('prod')(chain.get_forward_method('prod')(15)) == 15.0


def test_loopback(first_backward, second_backward, layer_builder):
    layer = PipelineLayer(first_backward, second_backward)

    wrapped = layer.get_loopback(lambda x: x, ['prod'], 'prod')
    assert wrapped(4) == 4

    wrapped = layer.get_loopback(lambda x: x * 2, ['prod'], 'prod')
    assert wrapped(4) == 49.

    def counter():
        nonlocal count
        count += 1
        return 5

    count = 0
    cross_pipes_checker = layer_builder.build_layer(
        prod=lambda prod, _counter: prod,
        inverse_prod=lambda prod, _counter: prod,
        _counter=counter
    )

    layer = PipelineLayer(layer, cross_pipes_checker)
    wrapped = layer.get_loopback(lambda x: x * 2, ['prod'], 'prod')
    assert wrapped(4) == 49.
    assert count == 1
