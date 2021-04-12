# Copyright (c) 2021 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from typing import Any, Dict, Optional, Callable, Union
import time
import ruamel.yaml
import ruamel.yaml.constructor
import yakut
from ._loader import YAMLLoader


class EmbeddedExpressionError(ValueError):
    """
    An invalid embedded expression found in the YAML document.
    """


class EvaluableYAMLLoader(YAMLLoader):
    """
    This is like regular loader except that it feeds scalars tagged as ``!$`` into :func:`eval`
    and substitutes them with the evaluation result.
    """

    EVAL_TAG = "!$"

    def __init__(self, evaluation_context: Dict[str, Any]) -> None:
        """
        :param evaluation_context: Objects that will be available to the evaluated expressions as their global scope.
        """
        super().__init__()
        self._evaluation_context = evaluation_context.copy()

        class ConstructorWrapper(ruamel.yaml.constructor.RoundTripConstructor):
            """
            New class to avoid global state: https://stackoverflow.com/questions/67041211
            """

        self._impl.Constructor = ConstructorWrapper
        self._impl.constructor.add_constructor(self.EVAL_TAG, construct_embedded_expression)

    def load(self, text: str, **evaluation_context: Any) -> Any:
        """
        Loads and evaluates the evaluable YAML in one operation.
        It is not recommended to use this method if the same YAML document needs to be evaluated multiple times
        (perhaps with different context values); for that, see :meth:`load_unevaluated`.
        This is a mere shortcut for ``self.load_unevaluated(doc)(**evaluation_context)``.
        """
        return self.load_unevaluated(text)(**evaluation_context)

    def load_unevaluated(self, text: str) -> Callable[..., Any]:
        """
        Loads the document without evaluation.
        The result is a closure that accepts keyword arguments that extend/override the evaluation context
        passed to the constructor.
        The result of that closure is the evaluated document.
        This way allows you to evaluate the same document with different arguments without re-parsing it from scratch.
        """
        root = self._impl.load(text)

        def evaluate(**kw: Any) -> Any:
            ctx = self._evaluation_context.copy()
            ctx.update(kw)

            def traverse(obj: Any) -> Any:
                if isinstance(obj, dict):
                    return {key: traverse(value) for key, value in obj.items()}
                if isinstance(obj, (list, tuple, set)):
                    return list(map(traverse, obj))
                if isinstance(obj, (bool, int, float)) or obj is None:
                    return obj
                if isinstance(obj, EmbeddedExpression):
                    return obj.evaluate(ctx)
                raise TypeError(f"Unexpected object type: {type(obj).__name__}")  # pragma: no cover

            return traverse(root)

        return evaluate


class EmbeddedExpression:
    """
    An evaluable expression embedded into a YAML document.
    """

    def __init__(self, source_text: str) -> None:
        self._source_text = source_text
        self._code = compile(self._source_text, "<embedded-yaml-expression>", "eval")

    def evaluate(self, evaluation_context: Dict[str, Any]) -> Any:
        started_at = time.monotonic()
        result = eval(self._code, evaluation_context)
        elapsed = time.monotonic() - started_at
        _logger.debug("Expression evaluated successfully in %.3f sec: %s", elapsed, self)
        return result

    def __repr__(self) -> str:
        return repr(self._source_text)


def construct_embedded_expression(_constructor: ruamel.yaml.Constructor, node: ruamel.yaml.Node) -> EmbeddedExpression:
    _logger.debug("Loading embedded expression from node %r", node)
    if not isinstance(node, ruamel.yaml.ScalarNode) or not isinstance(node.value, str):
        raise EmbeddedExpressionError("Embedded expression must be a YAML string")
    try:
        out = EmbeddedExpression(node.value)
    except Exception as ex:
        raise EmbeddedExpressionError(f"Could not load embedded expression from node {node}: {ex}") from ex
    _logger.debug("Successfully constructed embedded expression: %s", out)
    return out


_logger = yakut.get_logger(__name__)


def _unittest_eval() -> None:
    import pytest

    loader = EvaluableYAMLLoader({"one": 1, "two": 2})
    out = loader.load(
        "{a: 456, b: !$ one + 5, c: [!$ two, !$ foo - two]}",
        foo=3,
    )
    print(out)
    assert out == {"a": 456, "b": 6, "c": [2, 1]}

    with pytest.raises(EmbeddedExpressionError, match=r"(?i).*YAML string.*"):
        loader.load("baz: !$ []")

    with pytest.raises(EmbeddedExpressionError, match=r"(?i).*expression.*"):
        loader.load("baz: !$ 0syntax error")
